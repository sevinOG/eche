# tests/test_audit_log.py
# Every cog command and tool path must write an audit line. Secrets never
# appear. Safety-relevant events are not truncated. The file is append-only.
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
COGS = SOURCE / "cogs"
import sys
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


def _decorator_name(node: ast.AST) -> str | None:
    """Return 'command', 'group', or a 'group.command' attribute name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        if parent in {"commands", None}:
            return node.attr
        return f"{parent}.{node.attr}"
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _call_kw(node: ast.Call, key: str) -> str | None:
    for kw in node.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def discover_cog_commands(root: Path = COGS) -> list[str]:
    """Every Discord command declared under cogs/, including group subcommands."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        current_group: str | None = None
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            items = node.body if isinstance(node, ast.ClassDef) else [node]
            if isinstance(node, ast.ClassDef):
                for item in items:
                    _collect_command(item, found, current_group_holder := {"g": current_group})
                    current_group = current_group_holder["g"]
            else:
                _collect_command(node, found, {"g": current_group})
    # Unique, stable order.
    seen = set()
    out = []
    for name in found:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _collect_command(item, found: list[str], state: dict) -> None:
    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    for dec in item.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        kind = _decorator_name(dec)
        if kind is None:
            continue
        explicit = _call_kw(call, "name") if call else None
        func_name = item.name
        if kind in {"command", "group"} or kind.endswith(".command") or kind.endswith(".group"):
            name = explicit or func_name
            if kind == "group" or kind.endswith(".group"):
                state["g"] = name
                found.append(name)
            elif kind in {"command"}:
                found.append(name)
            elif kind.endswith(".command"):
                group = state.get("g")
                found.append(f"{group} {name}" if group else name)
            break


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="eche-audit-")
        self.root = Path(self.tmp.name)
        (self.root / "logs").mkdir()
        self.audit = self.root / "logs" / "audit.jsonl"
        self._old_root = os.environ.get("ECHE_USER_ROOT")
        self._old_path = os.environ.get("ECHE_AUDIT_PATH")
        os.environ["ECHE_USER_ROOT"] = str(self.root)
        os.environ.pop("ECHE_AUDIT_PATH", None)
        from core import audit as audit_mod
        self.audit_mod = audit_mod

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("ECHE_USER_ROOT", None)
        else:
            os.environ["ECHE_USER_ROOT"] = self._old_root
        if self._old_path is None:
            os.environ.pop("ECHE_AUDIT_PATH", None)
        else:
            os.environ["ECHE_AUDIT_PATH"] = self._old_path
        self.tmp.cleanup()

    def test_every_cog_command_writes_an_audit_line(self):
        names = discover_cog_commands()
        self.assertIn("image", names)
        self.assertIn("help", names)
        self.assertIn("ping", names)
        self.assertIn("shop buy", names)
        self.assertIn("bank value", names)
        self.assertIn("play", names)
        self.assertGreaterEqual(len(names), 30)
        from core.bot import Eche

        async def run():
            bot = object.__new__(Eche)
            for name in names:
                ctx = SimpleNamespace(
                    command=SimpleNamespace(qualified_name=name, name=name.split()[-1]),
                    author=SimpleNamespace(id=11),
                    guild=SimpleNamespace(id=22),
                    channel=SimpleNamespace(id=33),
                    message=SimpleNamespace(
                        author=SimpleNamespace(id=11),
                        guild=SimpleNamespace(id=22),
                        channel=SimpleNamespace(id=33),
                    ),
                )
                await Eche.on_command_completion(bot, ctx)

        asyncio.run(run())
        rows = [r for r in _records(self.audit) if r.get("event") == "command"]
        logged = {r.get("name") for r in rows}
        missing = [n for n in names if n not in logged]
        self.assertEqual(missing, [], f"{len(missing)} commands had no audit line: {missing}")
        for rec in rows:
            self.assertEqual(rec.get("user_id"), "11")
            self.assertEqual(rec.get("guild_id"), "22")
            self.assertEqual(rec.get("channel_id"), "33")
            self.assertEqual(rec.get("outcome"), "success")
            self.assertTrue(rec.get("ts"))

    def test_every_tool_path_writes_an_audit_line(self):
        import core.tools_admin  # noqa: F401
        import core.tools_context  # noqa: F401
        import core.tools_duckduckgo  # noqa: F401
        import core.tools_thread  # noqa: F401
        from core.tools import ToolContext, ToolResult, execute, registered_names

        names = sorted(registered_names())
        self.assertIn("duckduckgo", names)
        self.assertIn("thread", names)
        self.assertIn("context_raw", names)
        for required in ("kick", "ban", "timeout", "mute"):
            self.assertIn(required, names)
        self.assertGreaterEqual(len(names), 6)

        message = SimpleNamespace(
            author=SimpleNamespace(id=44),
            guild=SimpleNamespace(id=55),
            channel=SimpleNamespace(id=66),
        )
        ctx = ToolContext(bot=object(), message=message)

        async def dummy(ctx, arguments):
            return ToolResult(text="ok")

        from core import tools as tools_mod

        async def run():
            with mock.patch.object(tools_mod, "default_tools_enabled", return_value=True), \
                    mock.patch.object(tools_mod, "tool_prompt_enabled", return_value=True), \
                    mock.patch("core.admin_tools.admin_tools_enabled", return_value=True), \
                    mock.patch("core.admin_tools.author_is_owner", new=mock.AsyncMock(return_value=True)):
                original = dict(tools_mod._TOOLS)
                try:
                    for name, tool in list(tools_mod._TOOLS.items()):
                        tools_mod._TOOLS[name] = type(tool)(
                            name=tool.name,
                            description=tool.description,
                            handler=dummy,
                            parameters=tool.parameters,
                            access=tool.access,
                        )
                    for name in names:
                        await execute(name, {}, ctx)
                finally:
                    tools_mod._TOOLS.clear()
                    tools_mod._TOOLS.update(original)

        asyncio.run(run())
        rows = [r for r in _records(self.audit) if r.get("event") == "tool"]
        logged = {r.get("name") for r in rows}
        missing = [n for n in names if n not in logged]
        self.assertEqual(missing, [], f"{len(missing)} tools had no audit line: {missing}")
        for rec in rows:
            if rec.get("name") in names:
                self.assertEqual(rec.get("outcome"), "success")
                self.assertEqual(rec.get("user_id"), "44")
                self.assertEqual(rec.get("guild_id"), "55")
                self.assertEqual(rec.get("channel_id"), "66")
                self.assertTrue(rec.get("ts"))

    def test_block_logs_category_and_footer_hash(self):
        from core.blocklist import note
        from core.safety import footer_sha256

        self.audit_mod.bind(user_id=7, guild_id=8, channel_id=9)
        note("llm-input-always")
        rows = [r for r in _records(self.audit) if r.get("event") == "block"]
        self.assertTrue(rows)
        rec = rows[-1]
        self.assertEqual(rec.get("category"), "llm-input-always")
        self.assertEqual(rec.get("footer_hash"), footer_sha256())
        self.assertEqual(rec.get("outcome"), "refusal")
        self.assertEqual(rec.get("user_id"), "7")
        self.assertEqual(rec.get("channel_id"), "9")
        raw = self.audit.read_text(encoding="utf-8")
        self.assertNotIn("Safety rules (mandatory", raw)

    def test_api_logs_request_and_response_ids(self):
        headers = {"x-request-id": "req-abc", "x-groq-id": "groq-1"}
        response = SimpleNamespace(headers=headers, id="resp-xyz")
        self.audit_mod.api_from_http("groq", response, {"id": "chat-1"}, outcome="success")
        rec = [r for r in _records(self.audit) if r.get("event") == "api"][-1]
        self.assertEqual(rec.get("service"), "groq")
        self.assertEqual(rec.get("request_id"), "req-abc")
        self.assertEqual(rec.get("response_id"), "chat-1")
        self.assertEqual(rec.get("outcome"), "success")

    def test_safety_event_is_not_truncated(self):
        long_category = "classifier-unsafe-" + ("x" * 2000)
        self.audit_mod.block(long_category, footer_hash="abc123", user_id=1, guild_id=2, channel_id=3)
        raw = self.audit.read_text(encoding="utf-8")
        self.assertIn(long_category, raw)
        self.assertNotIn("…", raw.split(long_category)[0][-5:] + raw.split(long_category)[1][:5] if False else raw)
        self.assertGreater(len(raw), 500)

    def test_file_is_append_only(self):
        self.audit_mod.command("ping", "success", user_id=1, guild_id=2, channel_id=3)
        first = self.audit.read_text(encoding="utf-8")
        self.audit_mod.command("help", "success", user_id=1, guild_id=2, channel_id=3)
        second = self.audit.read_text(encoding="utf-8")
        self.assertTrue(second.startswith(first))
        self.assertEqual(len(_records(self.audit)), 2)

    def test_secrets_and_nonces_are_redacted(self):
        dirty = (
            "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz "
            "api_key=gsk_ABCDEFG nonce=deadbeef1234 token=supersecret "
            "OPENROUTER_API_KEY=sk-or-v1-hidden"
        )
        cleaned = self.audit_mod.redact(dirty)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", cleaned)
        self.assertNotIn("gsk_ABCDEFG", cleaned)
        self.assertNotIn("deadbeef1234", cleaned)
        self.assertNotIn("supersecret", cleaned)
        self.assertNotIn("sk-or-v1-hidden", cleaned)
        self.assertIn("[redacted]", cleaned)
        rec = self.audit_mod.record(
            "command",
            name="image",
            outcome="success",
            detail="Bearer sk-abcdefghijklmnopqrstuvwxyz nonce=zzz",
            user_id=1,
            guild_id=2,
            channel_id=3,
        )
        blob = self.audit.read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", blob)
        self.assertNotIn("deadbeef", blob.lower())
        self.assertNotIn("zzz", blob)
        self.assertNotIn("zzz", json.dumps(rec))

    def test_command_error_is_audited_as_error_or_refusal(self):
        from discord.ext import commands
        from core.bot import Eche

        class DummyErr(commands.CheckFailure):
            args = ()

        async def run():
            bot = object.__new__(Eche)
            ctx = SimpleNamespace(
                command=SimpleNamespace(qualified_name="forceoptin", name="forceoptin"),
                author=SimpleNamespace(id=1),
                guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3),
                send=mock.AsyncMock(),
            )
            await Eche.on_command_error(bot, ctx, DummyErr())

        asyncio.run(run())
        rec = [r for r in _records(self.audit) if r.get("event") == "command"][-1]
        self.assertEqual(rec.get("name"), "forceoptin")
        self.assertEqual(rec.get("outcome"), "refusal")

    def test_hooks_are_wired_in_source(self):
        bot = (SOURCE / "core" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("on_command_completion", bot)
        self.assertIn("command_from_ctx", bot)
        tools = (SOURCE / "core" / "tools.py").read_text(encoding="utf-8")
        self.assertIn("_audit_tool", tools)
        client = (SOURCE / "core" / "client.py").read_text(encoding="utf-8")
        self.assertIn("_audit_http", client)
        image = (SOURCE / "cogs" / "image_search.py").read_text(encoding="utf-8")
        self.assertIn("api_from_http", image)
        image_src = image
        # ?image itself is unchanged as a command; generic audit is the hook.
        self.assertIn('name="image"', image_src)


if __name__ == "__main__":
    unittest.main()
