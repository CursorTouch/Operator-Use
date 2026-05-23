from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import websockets
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.acp.server import OperatorACPAgent

logger = logging.getLogger(__name__)

DEFAULT_SIGNAL_URL = 'wss://0.peerjs.com/peerjs?key=peerjs'
DEFAULT_RTC_CONFIGURATION = RTCConfiguration(
    iceServers=[RTCIceServer(urls='stun:stun.l.google.com:19302')]
)
DATA_CHANNEL_LABEL = 'acp'


@dataclass(frozen=True)
class PeerIds:
    host: str
    client: str


def peer_ids(room: str) -> PeerIds:
    """Derive stable PeerJS IDs for a room."""
    safe_room = ''.join(c if c.isalnum() or c in ('-', '_') else '-' for c in room)
    return PeerIds(host=f'operator-host-{safe_room}', client=f'operator-client-{safe_room}')


def _signaling_url(base_url: str, peer_id: str) -> str:
    sep = '&' if '?' in base_url else '?'
    token = secrets.token_urlsafe(12)
    return f'{base_url}{sep}id={peer_id}&token={token}'


def _message_type(message: dict[str, Any]) -> str:
    return str(message.get('type') or '').upper()


def _payload_sdp(payload: dict[str, Any]) -> dict[str, Any]:
    # PeerJS wraps SDP as payload.sdp; the local test bridge may pass it directly.
    nested = payload.get('sdp')
    return nested if isinstance(nested, dict) else payload


async def _send_signal(
    ws: Any,
    *,
    msg_type: str,
    src: str,
    dst: str,
    payload: dict[str, Any],
) -> None:
    await ws.send(json.dumps({
        'type': msg_type,
        'src': src,
        'dst': dst,
        'payload': payload,
    }))


async def _add_ice_candidate(pc: RTCPeerConnection, payload: dict[str, Any]) -> None:
    candidate_payload = payload.get('candidate')
    if isinstance(candidate_payload, dict):
        candidate_sdp = candidate_payload.get('candidate', '')
        sdp_mid = candidate_payload.get('sdpMid')
        sdp_mline_index = candidate_payload.get('sdpMLineIndex')
    else:
        candidate_sdp = payload.get('candidate', '')
        sdp_mid = payload.get('sdpMid')
        sdp_mline_index = payload.get('sdpMLineIndex')

    if not candidate_sdp:
        await pc.addIceCandidate(None)
        return

    candidate = candidate_from_sdp(candidate_sdp)
    candidate.sdpMid = sdp_mid
    candidate.sdpMLineIndex = sdp_mline_index
    await pc.addIceCandidate(candidate)


async def _wait_for_ice_gathering(pc: RTCPeerConnection, timeout: float = 5.0) -> None:
    if pc.iceGatheringState == 'complete':
        return

    done = asyncio.Event()

    @pc.on('icegatheringstatechange')
    def on_icegatheringstatechange() -> None:
        if pc.iceGatheringState == 'complete':
            done.set()

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(done.wait(), timeout=timeout)


async def _open_socket_bridge() -> tuple[
    asyncio.StreamReader,
    asyncio.StreamWriter,
    asyncio.StreamReader,
    asyncio.StreamWriter,
]:
    sock_acp, sock_bridge = socket.socketpair()
    sock_acp.setblocking(False)
    sock_bridge.setblocking(False)

    acp_reader, acp_writer = await asyncio.open_connection(sock=sock_acp)
    bridge_reader, bridge_writer = await asyncio.open_connection(sock=sock_bridge)
    return acp_reader, acp_writer, bridge_reader, bridge_writer


def _write_channel_message(writer: asyncio.StreamWriter, message: Any) -> None:
    if isinstance(message, bytes):
        payload = message.strip()
    else:
        payload = str(message).encode('utf-8').strip()
    if payload:
        writer.write(payload + b'\n')


async def _drain_writer(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, RuntimeError):
        await writer.drain()


async def _forward_reader_to_channel(
    reader: asyncio.StreamReader,
    channel: Any,
    *,
    ready: asyncio.Event | None = None,
) -> None:
    if ready is not None:
        await ready.wait()

    while True:
        line = await reader.readline()
        if not line:
            break
        payload = line.strip()
        if payload:
            channel.send(payload.decode('utf-8'))


class ACPWebRTCServer:
    """
    Serves OperatorACPAgent over a WebRTC DataChannel.

    PeerJS is used only as signaling. Once SDP exchange completes, ACP JSON-RPC
    lines flow peer-to-peer through the DataChannel using Zed's ACP connection
    classes on both ends.
    """

    def __init__(
        self,
        agent: OperatorACPAgent,
        room: str,
        *,
        signal_url: str = DEFAULT_SIGNAL_URL,
    ) -> None:
        self._agent = agent
        self._room = room
        self._signal_url = signal_url
        ids = peer_ids(room)
        self._host_id = ids.host
        self._client_id = ids.client
        self._pc: RTCPeerConnection | None = None
        self._ws: Any = None
        self._tasks: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        url = _signaling_url(self._signal_url, self._host_id)
        logger.info('ACP WebRTC: connecting signaling peer=%s room=%s', self._host_id, self._room)

        async with websockets.connect(url) as ws:
            self._ws = ws
            logger.info('ACP WebRTC: waiting for client peer=%s', self._client_id)
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = _message_type(data)
                if msg_type in ('OPEN', 'PING'):
                    continue
                if data.get('src') != self._client_id:
                    continue

                payload = data.get('payload') or {}
                if msg_type == 'OFFER':
                    await self._handle_offer(payload)
                elif msg_type == 'CANDIDATE' and self._pc is not None:
                    await _add_ice_candidate(self._pc, payload)
                elif msg_type in ('LEAVE', 'EXPIRE'):
                    logger.info('ACP WebRTC: client left room=%s', self._room)

    async def _handle_offer(self, offer_payload: dict[str, Any]) -> None:
        if self._pc is not None:
            await self._pc.close()

        pc = RTCPeerConnection(configuration=DEFAULT_RTC_CONFIGURATION)
        self._pc = pc

        acp_reader, acp_writer, bridge_reader, bridge_writer = await _open_socket_bridge()

        from acp.agent.connection import AgentSideConnection  # type: ignore[import-untyped]
        conn = AgentSideConnection(self._agent, acp_writer, acp_reader)
        listen_task = asyncio.create_task(conn.listen(), name=f'acp-webrtc-listen-{self._room}')
        self._tasks.add(listen_task)
        listen_task.add_done_callback(self._tasks.discard)

        @pc.on('datachannel')
        def on_datachannel(channel: Any) -> None:
            if channel.label != DATA_CHANNEL_LABEL:
                logger.debug('ACP WebRTC: ignoring data channel label=%s', channel.label)
                return

            logger.info('ACP WebRTC: data channel open room=%s', self._room)

            @channel.on('message')
            def on_message(message: Any) -> None:
                _write_channel_message(bridge_writer, message)
                asyncio.create_task(_drain_writer(bridge_writer))

            forward_task = asyncio.create_task(
                _forward_reader_to_channel(bridge_reader, channel),
                name=f'acp-webrtc-forward-{self._room}',
            )
            self._tasks.add(forward_task)
            forward_task.add_done_callback(self._tasks.discard)

        @pc.on('connectionstatechange')
        async def on_connectionstatechange() -> None:
            logger.info('ACP WebRTC: connection state=%s room=%s', pc.connectionState, self._room)
            if pc.connectionState in ('failed', 'closed', 'disconnected'):
                for writer in (acp_writer, bridge_writer):
                    with contextlib.suppress(Exception):
                        writer.close()

        sdp = _payload_sdp(offer_payload)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp['sdp'], type=sdp['type']))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await _wait_for_ice_gathering(pc)

        if self._ws is None:
            raise RuntimeError('ACP WebRTC signaling socket is not connected')

        await _send_signal(
            self._ws,
            msg_type='ANSWER',
            src=self._host_id,
            dst=self._client_id,
            payload={
                'type': 'data',
                'sdp': {
                    'type': pc.localDescription.type,
                    'sdp': pc.localDescription.sdp,
                },
            },
        )

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._pc is not None:
            await self._pc.close()


async def serve_webrtc(runtime: Runtime, room: str, *, signal_url: str = DEFAULT_SIGNAL_URL) -> None:
    """Create ACPWebRTCServer and serve until the process is interrupted."""
    from program.acp.server import OperatorACPAgent

    agent = OperatorACPAgent(runtime)
    server = ACPWebRTCServer(agent, room, signal_url=signal_url)
    try:
        await server.start()
    finally:
        await server.close()
