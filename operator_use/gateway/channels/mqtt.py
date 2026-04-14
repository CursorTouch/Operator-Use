"""MQTT channel — connects IoT sensors and devices to the agent via an MQTT broker.

Topic convention (default prefix = "operator"):
  Incoming:  {prefix}/in/{device_id}   → agent receives message from device
  Outgoing:  {prefix}/out/{device_id}  → agent sends response to device

Example:
  Sensor publishes "28.5°C" to "operator/in/temp_sensor"
  Agent receives it, processes it, responds on "operator/out/temp_sensor"
"""

import asyncio
import json
import logging
from typing import Optional

import aiomqtt

from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart, text_from_parts
from operator_use.gateway.channels.base import BaseChannel
from operator_use.gateway.channels.config import MQTTConfig

logger = logging.getLogger(__name__)


class MQTTChannel(BaseChannel):
    """MQTT channel. Subscribes to {prefix}/in/# and publishes responses to {prefix}/out/{device_id}."""

    def __init__(self, config: MQTTConfig, bus=None) -> None:
        super().__init__(config, bus)
        self._client: Optional[aiomqtt.Client] = None
        self._stop_event = asyncio.Event()

    def _cfg(self, key, default=None):
        return getattr(self.config, key, default)

    @property
    def name(self) -> str:
        return "mqtt"

    @property
    def _subscribe_topic(self) -> str:
        return f"{self._cfg('topic_prefix')}/in/#"

    def _publish_topic(self, device_id: str) -> str:
        return f"{self._cfg('topic_prefix')}/out/{device_id}"

    async def start(self) -> None:
        if not self._cfg("broker_host"):
            logger.warning("MQTT channel: broker_host not configured, skipping")
            return
        self._stop_event.clear()
        try:
            await self._listen()
        except Exception as e:
            logger.error(f"MQTT channel error: {e}", exc_info=True)

    async def stop(self) -> None:
        self._stop_event.set()

    async def _listen(self) -> None:
        host = self._cfg("broker_host")
        port = self._cfg("broker_port", 1883)
        username = self._cfg("username") or None
        password = self._cfg("password") or None
        client_id = self._cfg("client_id", "operator-agent")
        keepalive = self._cfg("keepalive", 60)
        tls = self._cfg("tls", False)

        logger.info(f"MQTT connecting to {host}:{port} (topic: {self._subscribe_topic})")

        tls_params = aiomqtt.TLSParameters() if tls else None

        async with aiomqtt.Client(
            hostname=host,
            port=port,
            username=username,
            password=password,
            identifier=client_id,
            keepalive=keepalive,
            tls_params=tls_params,
        ) as client:
            self._client = client
            await client.subscribe(self._subscribe_topic)
            logger.info(f"MQTT subscribed to {self._subscribe_topic}")

            async with client.messages() as messages:
                async for mqtt_message in messages:
                    if self._stop_event.is_set():
                        break
                    await self._handle_mqtt_message(mqtt_message)

        self._client = None

    async def _handle_mqtt_message(self, mqtt_message) -> None:
        """Convert an MQTT message into an IncomingMessage and push to bus."""
        topic = str(mqtt_message.topic)
        prefix = f"{self._cfg('topic_prefix')}/in/"

        # Extract device_id from topic: "operator/in/sensor1" → "sensor1"
        if topic.startswith(prefix):
            device_id = topic[len(prefix) :]
        else:
            device_id = topic

        if not device_id:
            return

        # Decode payload — try JSON first, fall back to plain text
        try:
            raw = mqtt_message.payload
            payload_str = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            payload_str = str(mqtt_message.payload)

        # If payload is JSON, pretty-print it so the agent gets structured context
        try:
            parsed = json.loads(payload_str)
            content = f"[MQTT device: {device_id}]\n{json.dumps(parsed, indent=2)}"
        except (json.JSONDecodeError, ValueError):
            content = f"[MQTT device: {device_id}] {payload_str}"

        logger.info(f"MQTT received from {device_id}: {payload_str[:80]}")

        incoming = IncomingMessage(
            channel="mqtt",
            chat_id=device_id,
            user_id=device_id,
            parts=[TextPart(content=content)],
        )
        await self.receive(incoming)

    async def send(self, message: OutgoingMessage) -> None:
        """Publish agent response back to the device's outgoing topic."""
        if not self._client:
            logger.warning("MQTT send: no active client connection")
            return

        device_id = message.chat_id
        topic = self._publish_topic(device_id)
        text = text_from_parts(message.parts) or ""

        try:
            await self._client.publish(topic, payload=text.encode("utf-8"))
            logger.info(f"MQTT published to {topic}: {text[:80]}")
        except Exception as e:
            logger.error(f"MQTT publish error: {e}")
