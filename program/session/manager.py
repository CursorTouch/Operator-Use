from __future__ import annotations
from program.session.types import SessionTreeNode
from program.message.types import AgentMessage, Role
from program.session.types import (
    SessionFileEntry, SessionHeader,
    SessionEntry, LabelEntry, CompactionEntry, 
    SessionOptions, SessionType, MessageEntry,
    ThinkingLevelChangeEntry, SessionInfoEntry,
    ModelChangeEntry, BranchEntry, CustomInfoEntry, 
    CustomMessageEntry, SessionContext, SessionInfo
)
from program.settings.paths import get_sessions_dir
from program.session.utils import (
    create_session_id, generate_timestamp, generate_id, read_session_file,
    is_valid_session_file, find_most_recent_session, is_message_with_contents,
    get_last_activity_time, get_session_modified_date, build_session_info,
    list_sessions_from_dir, get_default_session_dir
)
from program.message.types import AssistantMessage, CustomMessage, CompactionSummaryMessage, BranchSummaryMessage
from program.llm.types import ThinkingLevel
from program.llm.model import Model
from program.llm.provider import Provider
from program.message.types import ImageContent, TextContent, LLMMessage
from datetime import datetime
from typing import Any, Callable
from pathlib import Path

class SessionManager:
    def __init__(self,cwd:str|Path,session_dir:Path|None=None,session_file:Path|None=None,persist:bool=True):
        self.session_id:str|None=None
        self.cwd=Path(cwd).resolve()
        self.session_dir=(session_dir or get_sessions_dir()).resolve()
        self.session_file=session_file
        self.persist=persist
        self.by_id:dict[str,SessionEntry]=dict()
        self.labels_by_id:dict[str,str]=dict()
        self.label_timestamps_by_id:dict[str,float]=dict()
        self.leaf_id:str|None=None
        self.entries:list[SessionFileEntry]=[]
        self.flushed:bool=False

        if self.persist and not self.session_dir.exists(): 
            self.session_dir.mkdir(parents=True, exist_ok=True)
            
        if self.session_file:
            self.set_session(self.session_file)
        else:
            self.new_session()
            
    def set_session(self,session_file:Path):
        if self.session_file.exists():
            self.entries = read_session_file(session_file)

            if len(self.entries)==0:
                self.new_session()
                self.session_file=session_file
                self._rewrite_file()
                self.flushed=True


    def new_session(self,options:SessionOptions|None=None):
        options=options or SessionOptions()
        session_id=options.id or create_session_id()
        parent_session=Path(options.parent_session).resolve() if options.parent_session else None
        timestamp=generate_timestamp()
        header=SessionHeader(
            id=session_id,
            timestamp=timestamp,
            cwd=self.cwd,
            parent_session=parent_session
        )

        self.entries=[header]
        self.by_id.clear()
        self.labels_by_id.clear()
        self.leaf_id=None
        self.flushed=False

        if (self.persist):
            file_timestamp=datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            self.session_file = (self.session_dir / f"{file_timestamp}_{session_id}.jsonl").resolve()
        
        return self.session_file
        
    def _rewrite_file(self):
        if not self.persist or not self.session_file:
            return None

        lines=[entry.model_dump_json(exclude_none=True) for entry in self.entries]
        self.session_file.write_text("\n".join(lines),encoding="utf-8")
        
    def build_index(self):
        self.by_id.clear()
        self.labels_by_id.clear()
        self.leaf_id=None

        for entry in self.entries[1:]:
            match entry:
                case LabelEntry(label=label, timestamp=timestamp, target_id=target_id):
                    if label:
                        self.labels_by_id[target_id]=label
                        self.label_timestamps_by_id[target_id]=timestamp
                    else:
                        self.labels_by_id.pop(target_id,None)
                        self.label_timestamps_by_id.pop(target_id,None)
                case _:
                    continue

    def _persist(self,entry: SessionEntry):
        if not self.persist or not self.session_file:
            return None

        has_assistant_message= any(isinstance(e,MessageEntry) and isinstance(e.message,AssistantMessage) for e in self.entries)

        if not has_assistant_message:
            self.flushed=False
            return 

        with self.session_file.open("a", encoding="utf-8") as f:
            if not self.flushed:
                lines=[entry.model_dump_json(exclude_none=True)+"\n" for entry in self.entries]
                f.writelines(lines)
                self.flushed=True
            else:
                f.write(entry.model_dump_json(exclude_none=True)+"\n")

    
    def _append_entry(self,entry:SessionEntry):
        """Appends an entry to the session and flushes it to disk if the session has an assistant message."""
        self.entries.append(entry)
        self.by_id[entry.id]=entry
        self.leaf_id=entry.id
        self._persist(entry)

    def append_message(self,message:AgentMessage):
        entry=MessageEntry(message=message,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_thinking_level_change(self,thinking_level:ThinkingLevel):
        entry=ThinkingLevelChangeEntry(thinking_level=thinking_level,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_model_change(self,model:Model,provider:Provider):
        entry=ModelChangeEntry(model=model,provider=provider,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_compaction(self,summary:str,retained_from_id:str,tokens_before:int,details:Any|None=None):
        entry=CompactionEntry(summary=summary,retained_from_id=retained_from_id,tokens_before=tokens_before,details=details,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_label_change(self,target_id:str,label:str|None=None):
        entry=LabelEntry(target_id=target_id,label=label,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_custom_info(self,custom_type:str,data:Any|None=None):
        entry=CustomInfoEntry(custom_type=custom_type,data=data,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_custom_message(self,custom_type:str,contents:list[ImageContent|TextContent],details:Any|None=None):
        entry=CustomMessageEntry(custom_type=custom_type,contents=contents,details=details,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_session_info(self,name:str):
        entry=SessionInfoEntry(name=name,parent_id=self.leaf_id)
        self._append_entry(entry)

    def append_custom_message(self, message:CustomMessage):
        entry=CustomMessageEntry(**message.model_dump(),parent_id=self.leaf_id)
        self._append_entry(entry)        

    def get_session_name(self):
        """Get the session name."""
        for entry in reversed(self.entries):
            if entry.type==SessionType.SESSION_INFO and entry.name.strip():
                return entry.name.strip()
        return None


    def get_leaf_id(self)->str|None:
        return self.leaf_id

    def get_leaf_entry(self)->SessionEntry|None:
        return self.by_id.get(self.leaf_id) if self.leaf_id else None

    def get_entry(self,id:str)->SessionEntry|None:
        return self.by_id.get(id)

    def get_children(self, parent_id:str)->list[SessionEntry]:
        return sorted([entry for entry in self.entries if entry.parent_id == parent_id], key=lambda entry: entry.timestamp)

    def get_label(self,id:str)->str|None:
        return self.labels_by_id.get(id)

    def get_branch(self,from_id:str,reverse=False)->list[SessionEntry]:
        path:list[SessionEntry] = []
        cursor=from_id or self.leaf_id
        # leaf -> root
        while cursor:
            current_entry=self.by_id.get(cursor)
            if not current_entry:
                break
            path.append(current_entry)
            cursor=current_entry.parent_id
        
        if reverse:
            # entries is now root -> leaf
            path.reverse()

        return path

    def build_session_context(self)->SessionContext:
        thinking_level:ThinkingLevel=ThinkingLevel.Off
        model:Model|None=None
        provider:Provider|None=None
        messages:list[AgentMessage]=[]
        compaction:CompactionEntry|None=None

        entries=self.get_branch(reverse=True) # [root ~> leaf]

        if not entries:
            return SessionContext(
                messages=messages,
                thinking_level=thinking_level,
                model=model,
                provider=provider
            )

        for entry in entries:
            match entry:
                case ThinkingLevelChangeEntry():
                    thinking_level=entry.thinking_level
                case ModelChangeEntry():
                    model=entry.model
                    provider=entry.provider
                case CompactionEntry():
                    compaction=entry

        def append_message(entry: SessionEntry):
            match entry:
                case MessageEntry(message=message):
                    messages.append(message)
                case CustomMessageEntry():
                    messages.append(CustomMessage.from_session(entry=entry))
                case BranchEntry():
                    messages.append(BranchSummaryMessage.from_session(entry=entry))
                    

        # If no compaction found
        if not compaction:
            for entry in entries:
                append_message(entry)
            return SessionContext(
                messages=messages,
                thinking_level=thinking_level,
                model=model,
                provider=provider
            )

        # append compaction summary message
        messages.append(CompactionSummaryMessage.from_session(compaction))   

        compaction_idx=entries.index(compaction)

        found_retained_from=False
        # before compaction but start from the retained_from_id
        for entry in entries[:compaction_idx]:
            if entry.id==compaction.retained_from_id:
                found_retained_from=True
            if found_retained_from:
                append_message(entry)

        # messages after compaction
        for entry in entries[compaction_idx+1:]:
            append_message(entry)

        return SessionContext(
            messages=messages,
            thinking_level=thinking_level,
            model=model,
            provider=provider
        )

    def get_header(self)->SessionHeader|None:
        header:SessionHeader|None=None
        for entry in self.entries:
            match entry:
                case SessionHeader():
                    header = entry
                    break
        return header
    
    def get_entries(self):
        entries:list[SessionEntry] = []
        for entry in self.entries:
            match entry:
                case SessionHeader():
                    continue
                case _:
                    entries.append(entry)
        return entries

    def get_tree(self)->SessionTreeNode:
        node_map:dict[str,SessionTreeNode]=dict()
        roots:list[SessionTreeNode]=[]

        for entry in self.entries:
            label = self.labels_by_id.get(entry.id)
            label_timestamp = self.label_timestamps_by_id.get(entry.id)
            
            node_map[entry.id]=SessionTreeNode(
                entry=entry,
                children=[],
                timestamp=label_timestamp,
                label=label
            )

        for entry in self.entries:
            node = node_map[entry.id]
            if entry.parent_id is None or entry.parent_id==entry.id:
                roots.append(node)
            else:
                parent_node=node_map.get(entry.parent_id)
                if parent_node is None:
                    roots.append(node)
                else:
                    parent_node.children.append(node)

        stack = roots.copy()

        while stack:
            node = stack.pop()
            node.children.sort(key=lambda child: child.entry.timestamp)
            stack.extend(node.children)
            
        roots.sort(key=lambda node: node.entry.timestamp)
        return roots

        
    def branch(self, from_id:str):
        if from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found.")
        
        self.leaf_id=from_id

    def reset_leaf(self):
        self.leaf_id = None

    def branch_with_summary(self,summary:str, from_id:str|None=None, details:Any|None = None):

        if from_id is not None and from_id not in self.by_id:
            raise KeyError(f"Entry {from_id} not found.")
        
        self.leaf_id=from_id

        entry=BranchEntry(
            parent_id=from_id,
            from_id=from_id or 'root',
            summary=summary,
            details=details
        )
        self._append_entry(entry)

    def create_branched_session(self,leaf_id: str) -> Path | None:
        
        previous_session_file = self.session_file
        path = self.get_branch(leaf_id, reverse=True)

        if not path:
            raise ValueError(
                f"Entry {leaf_id} not found."
            )

        path_without_labels = [
            entry
            for entry in path
            if not isinstance(entry, LabelEntry)
        ]

        session_id = create_session_id()

        timestamp = generate_timestamp()

        new_session_file = self.session_dir/ f"{timestamp}_{session_id}.jsonl"
        header = SessionHeader(
            id=session_id,
            timestamp=timestamp,
            cwd=self.cwd,
            parent_session=(
                previous_session_file
                if self.persist
                else None
            )
        )

        path_entry_ids = {entry.id for entry in path_without_labels}
        labels_to_write: list[tuple[str, str, float]] = []

        for target_id, label in self.labels_by_id.items():
            if target_id in path_entry_ids:
                labels_to_write.append(
                    (
                        target_id,
                        label,
                        self.label_timestamps_by_id[target_id]
                    )
                )

        label_entries: list[LabelEntry] = []
        last_entry = path_without_labels[-1] if path_without_labels else None
        parent_id = last_entry.id if last_entry else ""
        used_ids = set(path_entry_ids)

        for (target_id,label,label_timestamp) in labels_to_write:

            label_entry = LabelEntry(
                id=generate_id(used_ids),
                parent_id=parent_id,
                timestamp=label_timestamp,
                target_id=target_id,
                label=label
            )

            used_ids.add(label_entry.id)
            label_entries.append(label_entry)
            parent_id = label_entry.id

        self.entries = [
            header,
            *path_without_labels,
            *label_entries
        ]

        self.session_id = session_id

        self.session_file = new_session_file if self.persist else None

        self.build_index()

        has_assistant = any(isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage) for entry in self.entries)

        if self.persist:
            if has_assistant:
                self._rewrite_file()
                self.flushed = True
            else:
                self.flushed = False
            return new_session_file
        return None

    @classmethod
    def create(cls, cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir(cwd)
        return SessionManager(cwd, session_dir)

    @staticmethod
    def open(path: Path | str, session_dir: Path | str | None = None, cwd_override: Path | str | None = None) -> SessionManager:
        path = Path(path).resolve()
        entries = read_session_file(path)
        header = [entry for entry in entries if isinstance(entry, SessionHeader)][0]
        cwd = Path(cwd_override).resolve() if cwd_override else (Path(header.cwd).resolve() if header.cwd else Path.cwd())
        session_dir = Path(session_dir).resolve() if session_dir else path.parent
        return SessionManager(cwd, session_dir, path)

    @staticmethod
    def continue_recent(cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir(cwd)
        most_recent = find_most_recent_session(session_dir)
        if most_recent:
            return SessionManager(cwd, session_dir, most_recent)
        return SessionManager(cwd, session_dir)

    @staticmethod
    def in_memory(cwd:Path|None=None)->SessionManager:
        cwd=cwd or Path.cwd()
        return SessionManager(cwd,None,None,False)
    
    @staticmethod
    def fork_from(source: Path | str, target_cwd: Path | str, session_dir: Path | str | None = None) -> SessionManager:
        source = Path(source).resolve()
        target_cwd = Path(target_cwd).resolve()
        source_entries = read_session_file(source)
        if len(source_entries) == 0:
            raise ValueError(f"Cannot fork: source session file is empty or invalid: {source}")

        if not isinstance(source_entries[0], SessionHeader):
            raise ValueError(f"Cannot fork: source session has no header: {source}")

        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir(target_cwd)
        session_dir.mkdir(parents=True, exist_ok=True)

        new_session_id = create_session_id()
        timestamp = generate_timestamp()
        file_timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")

        new_session_file = session_dir / f"{file_timestamp}_{new_session_id}.jsonl"

        new_header = SessionHeader(
            id=new_session_id,
            timestamp=timestamp,
            cwd=target_cwd,
            parent_session=source
        )

        new_session_file.write_text(new_header.model_dump_json()+"\n")

        for entry in source_entries:
            match entry:
                case SessionHeader():
                    continue
                case _:
                    new_session_file.write_text(entry.model_dump_json()+"\n")
        
        return SessionManager(target_cwd,session_dir,new_session_file)

    
    @staticmethod
    def list(cwd: Path | str, session_dir: Path | str | None = None, on_progress: Callable[[int, int], None] | None = None) -> list[SessionInfo]:
        cwd = Path(cwd).resolve()
        session_dir = Path(session_dir).resolve() if session_dir else get_default_session_dir(cwd)
        sessions = list_sessions_from_dir(session_dir, on_progress=on_progress)
        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions

    @staticmethod
    def list_all(on_progress: Callable[[int, int], None] | None = None) -> list[SessionInfo]:
        sessions_dir = get_sessions_dir()
        if not sessions_dir.exists():
            return []

        sessions: list[SessionInfo] = []
        try:
            for cwd_dir in sessions_dir.iterdir():
                if cwd_dir.is_dir():
                    dir_sessions = list_sessions_from_dir(cwd_dir, on_progress=on_progress)
                    sessions.extend(dir_sessions)
        except Exception:
            pass

        sessions.sort(key=lambda s: s.modified.timestamp(), reverse=True)
        return sessions
        





        

            
        
                