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
    from core import audit as audit_mod
    return list(audit_mod.iter_records(str(path)))


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
        try:
            audit_mod.consume_refusal()
        except Exception:
            pass
        audit_mod._refusal.set(False)

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
        self.assertIn("on_message_edit", bot)
        self.assertIn("on_app_command_completion", bot)
        self.assertIn("on_error", bot)
        self.assertIn("on_app_command_error", bot)
        self.assertIn("on_command_error", bot)
        events = (SOURCE / "cogs" / "events" / "on_message.py").read_text(encoding="utf-8")
        self.assertIn("audit_chat", events)

    def test_safety_write_error_is_loud(self):
        alerts = []
        def boom(*a, **k):
            raise OSError("disk full")
        with mock.patch.object(self.audit_mod, "_write_once", side_effect=boom), \
             mock.patch.object(self.audit_mod, "_alert_write_failure", side_effect=lambda exc, safety: alerts.append((str(exc), safety))):
            with self.assertRaises(OSError):
                self.audit_mod.block("llm-input-always", user_id=1, channel_id=2)
        self.assertTrue(alerts)
        self.assertTrue(alerts[0][1])

    def test_ordinary_write_is_best_effort_after_alert(self):
        def boom(*a, **k):
            raise OSError("disk full")
        with mock.patch.object(self.audit_mod, "_write_once", side_effect=boom):
            rec = self.audit_mod.command("ping", "success", user_id=1, channel_id=2)
        self.assertEqual(rec.get("name"), "ping")
        self.assertTrue((self.root / "logs" / "audit.alert").exists())

    def test_rotate_renames_without_truncating_blocks(self):
        self.audit_mod.AUDIT_MAX_BYTES = 200
        try:
            self.audit_mod.block("keep-me-" + ("x" * 80), user_id=1, channel_id=2)
            first = self.audit.read_text(encoding="utf-8")
            self.audit_mod.block("second-block-" + ("y" * 80), user_id=1, channel_id=2)
        finally:
            self.audit_mod.AUDIT_MAX_BYTES = 5 * 1024 * 1024
        rotated = list((self.root / "logs").glob("audit.jsonl.*"))
        self.assertTrue(rotated, "expected a renamed archive")
        archive_text = rotated[0].read_text(encoding="utf-8")
        current = self.audit.read_text(encoding="utf-8") if self.audit.exists() else ""
        combined = archive_text + current
        self.assertIn("keep-me-", combined)
        self.assertIn("second-block-", combined)
        # Nothing was truncated away
        self.assertIn("x" * 80, combined)

    def test_value_scrub_covers_webhooks_and_bot_tokens(self):
        webhook = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDEFGH"
        token = ".".join((
            "MTIzNDU2Nzg5MDEyMzQ1Njc4",
            "GHijkl",
            "mnopqrstuvwxyzABCDEFGHIJKLMNO123456",
        ))
        cleaned = self.audit_mod.redact(f"hook={webhook} raw={token}")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyzABCDEFGH", cleaned)
        self.assertNotIn(token, cleaned)
        self.assertIn("[redacted]", cleaned)
        with mock.patch.dict(os.environ, {"DISCORD_TOKEN": "super-secret-discord-token-value"}):
            cleaned = self.audit_mod.redact("leaked super-secret-discord-token-value in a log")
            self.assertNotIn("super-secret-discord-token-value", cleaned)

    def test_chat_event_and_torn_last_line(self):
        self.audit_mod.chat(kind="mention", user_id=11, channel_id=22)
        rec = [r for r in _records(self.audit) if r.get("event") == "chat"][-1]
        self.assertEqual(rec.get("kind"), "mention")
        self.assertEqual(rec.get("user_id"), "11")
        with self.audit.open("a", encoding="utf-8") as fh:
            fh.write('{"event":"torn","ts":"')
        rows = _records(self.audit)
        self.assertTrue(any(r.get("event") == "chat" for r in rows))
        self.assertFalse(any(r.get("event") == "torn" for r in rows))

    def test_command_completion_logs_refusal_after_guardrail(self):
        self.audit_mod.block("llm-output-always", user_id=1, channel_id=2)
        self.audit_mod.command("ask", "success", user_id=1, channel_id=2)
        rec = [r for r in _records(self.audit) if r.get("event") == "command"][-1]
        self.assertEqual(rec.get("outcome"), "refusal")
        self.assertEqual(rec.get("name"), "ask")


    def test_ids_are_not_value_scrubbed(self):
        """HOME_SERVER_ID and ECHE_OWNER_ID must stay visible as guild_id/user_id."""
        owner = "123456789012345678"
        home = "987654321098765432"
        with mock.patch.dict(os.environ, {"ECHE_OWNER_ID": owner, "HOME_SERVER_ID": home, "GROQ_MODEL": "llama-3-8b"}):
            rec = self.audit_mod.command(
                "image",
                "success",
                user_id=owner,
                guild_id=home,
                channel_id="555555555555555555",
            )
        self.assertEqual(rec.get("user_id"), owner)
        self.assertEqual(rec.get("guild_id"), home)
        raw = self.audit.read_text(encoding="utf-8")
        self.assertIn(owner, raw)
        self.assertIn(home, raw)
        self.assertNotIn("[redacted]", rec.get("user_id"))
        self.assertNotIn("[redacted]", rec.get("guild_id"))
        # Model names from ENV_MAP must not be treated as secrets either,
        # but they should not appear unless we logged them; the ids stayed.

    def test_versioned_webhook_and_xai_key_are_redacted(self):
        v10 = "https://discord.com/api/v10/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDEFGH"
        v9 = "https://discord.com/api/v9/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyzABCDEFGH"
        key = "xai-abcdefghijklmnopqrstuvwxyz012345"
        cleaned = self.audit_mod.redact(f"url={v10} also={v9} key={key}")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyzABCDEFGH", cleaned)
        self.assertNotIn(key, cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_archive_cap_keeps_safety_lines_and_rotates_fallback(self):
        folder = self.root / "logs"
        # Three tiny archives of mixed content; cap is 1 file / tiny size.
        for i in range(3):
            path = folder / f"audit.jsonl.2020010{i}-000000"
            path.write_text(
                '{"event":"command","outcome":"success","name":"ping"}\n'
                '{"event":"block","outcome":"refusal","category":"keep-me","safety":true}\n',
                encoding="utf-8",
            )
        with mock.patch.object(self.audit_mod, "AUDIT_MAX_ARCHIVES", 1), \
             mock.patch.object(self.audit_mod, "AUDIT_MAX_ARCHIVE_BYTES", 50):
            self.audit_mod._prune_archives(str(folder))
        safety_hits = 0
        command_hits = 0
        for path in folder.glob("audit.jsonl.*"):
            body = path.read_text(encoding="utf-8")
            safety_hits += body.count("keep-me")
            command_hits += body.count('"name":"ping"')
        self.assertGreaterEqual(safety_hits, 3)
        self.assertEqual(command_hits, 0)

        # Fallback rotation uses the same helper.
        fallback = folder / "audit.fallback.jsonl"
        fallback.write_bytes(b"x" * (self.audit_mod.AUDIT_MAX_BYTES + 10))
        self.audit_mod._rotate_if_needed(str(fallback))
        self.assertFalse(fallback.exists() and fallback.stat().st_size > self.audit_mod.AUDIT_MAX_BYTES)
        self.assertTrue(any(folder.glob("audit.fallback.jsonl.*")))

    def test_icacls_nonzero_falls_back_to_chmod_and_alerts(self):
        from core import secrets as secrets_mod

        target = self.root / "logs" / "acl-target"
        target.write_text("x", encoding="utf-8")
        fake = mock.Mock(returncode=1, stdout="", stderr="access denied")
        with mock.patch.object(secrets_mod.sys, "platform", "win32"), \
             mock.patch("subprocess.run", return_value=fake), \
             mock.patch("getpass.getuser", return_value="tester"):
            with self.assertRaises(RuntimeError):
                secrets_mod._restrict_acl_windows(str(target))
        # chmod fallback already ran; audit ACL path should alert
        with mock.patch.object(secrets_mod.sys, "platform", "win32"), \
             mock.patch("subprocess.run", return_value=fake), \
             mock.patch("getpass.getuser", return_value="tester"):
            self.audit_mod._apply_acl(str(target))
        alert = self.root / "logs" / "audit.alert"
        self.assertTrue(alert.exists(), "expected an ACL failure alert")
        self.assertIn("ACL", alert.read_text(encoding="utf-8"))

    def test_logs_directory_gets_an_acl(self):
        src = (SOURCE / "core" / "audit.py").read_text(encoding="utf-8")
        self.assertIn("_ensure_log_dir", src)
        self.assertIn("_apply_acl(folder)", src)
        # Creating a path applies the directory ACL helper.
        path = self.audit_mod.audit_path()
        self.assertTrue(Path(path).parent.is_dir())
        mode = Path(path).parent.stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)

    def test_message_edit_skips_bot_and_self(self):
        from core.bot import Eche

        human = SimpleNamespace(id=11, bot=False)
        bot_user = SimpleNamespace(id=99, bot=True)
        me = SimpleNamespace(id=42, bot=True)

        async def run():
            bot = SimpleNamespace(user=me)
            after_bot = SimpleNamespace(
                author=bot_user, guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3), content="edited",
            )
            after_self = SimpleNamespace(
                author=me, guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3), content="edited",
            )
            after_embed = SimpleNamespace(
                author=human, guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3), content="same",
            )
            after_human = SimpleNamespace(
                author=human, guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3), content="new text",
            )
            before = SimpleNamespace(content="same")
            await Eche.on_message_edit(bot, before, after_bot)
            await Eche.on_message_edit(bot, before, after_self)
            await Eche.on_message_edit(bot, after_embed, after_embed)
            await Eche.on_message_edit(bot, before, after_human)

        asyncio.run(run())
        rows = [r for r in _records(self.audit) if r.get("event") == "message_edit"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("user_id"), "11")

    def test_rotate_permission_error_still_appends_safety(self):
        self.audit.write_bytes(b"y" * 100)
        with mock.patch.object(self.audit_mod, "AUDIT_MAX_BYTES", 10), \
             mock.patch("os.rename", side_effect=PermissionError("in use")):
            self.audit_mod.block("still-logged", footer_hash="abc", user_id=1, channel_id=2)
        raw = self.audit.read_text(encoding="utf-8")
        self.assertIn("still-logged", raw)

    def test_exception_inside_handler_still_logs(self):
        from discord.ext import commands
        from core.bot import Eche

        async def run():
            bot = object.__new__(Eche)
            ctx = SimpleNamespace(
                command=SimpleNamespace(qualified_name="ping", name="ping"),
                author=SimpleNamespace(id=1),
                guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3),
                send=mock.AsyncMock(),
            )
            await Eche.on_command_error(bot, ctx, RuntimeError("boom"))
            await Eche.on_error(bot, "on_message")
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=1),
                guild=SimpleNamespace(id=2),
                channel=SimpleNamespace(id=3),
                command=SimpleNamespace(qualified_name="slash", name="slash"),
            )
            await Eche.on_app_command_error(bot, interaction, RuntimeError("slash failed"))

        asyncio.run(run())
        rows = _records(self.audit)
        self.assertTrue(any(r.get("event") == "command" and r.get("outcome") == "error" and r.get("name") == "ping" for r in rows))
        self.assertTrue(any(r.get("event") == "error" and r.get("source") == "on_message" for r in rows))
        self.assertTrue(any(r.get("event") == "error" and r.get("source") == "app_command" for r in rows))
        self.assertTrue(any(r.get("event") == "command" and r.get("name") == "slash" and r.get("outcome") == "error" for r in rows))


class AuditCompletenessTests(AuditLogTests):
    """Discover registered commands, slash commands, tools, and listeners at runtime."""

    def test_runtime_discovery_success_and_error_paths(self):
        import core.tools_admin  # noqa: F401
        import core.tools_context  # noqa: F401
        import core.tools_duckduckgo  # noqa: F401
        import core.tools_thread  # noqa: F401
        from core.bot import Eche
        from core.tools import ToolContext, ToolResult, execute, registered_names
        from core import tools as tools_mod
        from discord.ext import commands

        async def run():
            # Lightweight bot: load the same extensions Eche does, without
            # starting background loops or logging in.
            import discord
            from discord.ext import commands as dcmds
            import re as _re

            bot_src = (SOURCE / "core" / "bot.py").read_text(encoding="utf-8")
            extensions = _re.findall(r'"((?:cogs)\.[A-Za-z0-9_.]+)"', bot_src)
            # Keep unique order from setup_hook's list.
            seen_ext = set()
            ext_list = []
            for ext in extensions:
                if ext not in seen_ext:
                    seen_ext.add(ext)
                    ext_list.append(ext)

            intents = discord.Intents.none()
            bot = dcmds.Bot(command_prefix="?", intents=intents, help_command=None)
            for ext in ext_list:
                try:
                    await bot.load_extension(ext)
                except Exception:
                    pass

            command_names = sorted({c.qualified_name for c in bot.walk_commands()})
            # AST discovery is a floor in case a cog failed to import.
            for name in discover_cog_commands():
                if name not in command_names:
                    command_names.append(name)

            app_names = []
            try:
                for cmd in bot.tree.walk_commands():
                    app_names.append(getattr(cmd, "qualified_name", None) or cmd.name)
            except Exception:
                try:
                    app_names = [c.name for c in bot.tree.get_commands()]
                except Exception:
                    app_names = []

            tool_names = sorted(registered_names())

            listener_events = set()
            extra = getattr(bot, "extra_events", {}) or {}
            for ev, funcs in extra.items():
                if funcs:
                    listener_events.add(ev)
            for cog in bot.cogs.values():
                for ev, _fn in cog.get_listeners():
                    listener_events.add(ev)
            for name in dir(type(bot)):
                if name.startswith("on_") and callable(getattr(Eche, name, None)):
                    if name in {
                        "on_command_completion", "on_command_error",
                        "on_message", "on_message_edit",
                        "on_app_command_completion", "on_app_command_error",
                        "on_error",
                    } or name in extra:
                        listener_events.add(name)

            # --- commands: success and error ---
            for name in command_names:
                ctx = SimpleNamespace(
                    command=SimpleNamespace(qualified_name=name, name=name.split()[-1]),
                    author=SimpleNamespace(id=11),
                    guild=SimpleNamespace(id=22),
                    channel=SimpleNamespace(id=33),
                    invoked_with=name.split()[-1],
                    send=mock.AsyncMock(),
                    message=SimpleNamespace(
                        author=SimpleNamespace(id=11),
                        guild=SimpleNamespace(id=22),
                        channel=SimpleNamespace(id=33),
                    ),
                )
                await Eche.on_command_completion(bot, ctx)
                await Eche.on_command_error(bot, ctx, RuntimeError("test-fail"))

            # --- slash / app commands ---
            if not app_names:
                app_names = ["(no-slash-registered)"]
                # Still exercise the hooks so the error/success path is covered.
            for name in app_names:
                interaction = SimpleNamespace(
                    user=SimpleNamespace(id=11),
                    guild=SimpleNamespace(id=22),
                    channel=SimpleNamespace(id=33),
                    command=SimpleNamespace(qualified_name=name, name=name),
                )
                command_obj = SimpleNamespace(qualified_name=name, name=name)
                await Eche.on_app_command_completion(bot, interaction, command_obj)
                await Eche.on_app_command_error(bot, interaction, RuntimeError("slash-fail"))

            # --- tools: success and error (handler exception) ---
            message = SimpleNamespace(
                author=SimpleNamespace(id=11),
                guild=SimpleNamespace(id=22),
                channel=SimpleNamespace(id=33),
            )
            tctx = ToolContext(bot=bot, message=message)

            async def ok(_ctx, _args):
                return ToolResult(text="ok")

            async def boom(_ctx, _args):
                raise RuntimeError("tool exploded")

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
                            handler=ok,
                            parameters=tool.parameters,
                            access=tool.access,
                        )
                    for name in tool_names:
                        await execute(name, {}, tctx)
                    for name, tool in list(tools_mod._TOOLS.items()):
                        tools_mod._TOOLS[name] = type(tool)(
                            name=tool.name,
                            description=tool.description,
                            handler=boom,
                            parameters=tool.parameters,
                            access=tool.access,
                        )
                    for name in tool_names:
                        await execute(name, {}, tctx)
                finally:
                    tools_mod._TOOLS.clear()
                    tools_mod._TOOLS.update(original)

            # --- listeners: success via real hooks; error via on_error ---
            human = SimpleNamespace(
                author=SimpleNamespace(id=11, bot=False),
                guild=SimpleNamespace(id=22),
                channel=SimpleNamespace(id=33),
                content="hello completeness",
                id=44,
            )
            try:
                await Eche.on_message(bot, human)
            except Exception:
                pass
            before = SimpleNamespace(content="old")
            after = SimpleNamespace(
                author=SimpleNamespace(id=11, bot=False),
                guild=SimpleNamespace(id=22),
                channel=SimpleNamespace(id=33),
                content="new",
            )
            try:
                await Eche.on_message_edit(bot, before, after)
            except Exception:
                pass
            for ev in sorted(listener_events):
                await Eche.on_error(bot, ev)

            # LLM turn + guardrail block (success and error / block)
            self.audit_mod.api("groq", request_id="req-1", response_id="resp-1", outcome="success")
            self.audit_mod.api("groq", outcome="error")
            self.audit_mod.block("llm-input-always", user_id=11, guild_id=22, channel_id=33)

            try:
                await bot.close()
            except Exception:
                pass
            return command_names, app_names, tool_names, sorted(listener_events)

        command_names, app_names, tool_names, listener_events = asyncio.run(run())
        rows = _records(self.audit)
        self.assertTrue(command_names, "no commands discovered at runtime")
        self.assertTrue(tool_names, "no tools discovered at runtime")
        self.assertTrue(listener_events, "no listeners discovered at runtime")

        command_ok = {(r.get("name"), r.get("outcome")) for r in rows if r.get("event") == "command"}
        missing_cmd = []
        for name in command_names:
            if (name, "success") not in command_ok:
                missing_cmd.append(f"{name}:success")
            if (name, "error") not in command_ok:
                missing_cmd.append(f"{name}:error")
        self.assertEqual(missing_cmd, [], f"commands missing audit records: {missing_cmd}")

        app_ok = {(r.get("name"), r.get("outcome")) for r in rows if r.get("event") == "command"}
        missing_app = []
        for name in app_names:
            if (name, "success") not in app_ok or (name, "error") not in app_ok:
                missing_app.append(name)
        self.assertEqual(missing_app, [], f"slash commands missing audit records: {missing_app}")

        tool_ok = {(r.get("name"), r.get("outcome")) for r in rows if r.get("event") == "tool"}
        missing_tool = []
        for name in tool_names:
            if (name, "success") not in tool_ok:
                missing_tool.append(f"{name}:success")
            if (name, "error") not in tool_ok:
                missing_tool.append(f"{name}:error")
        self.assertEqual(missing_tool, [], f"tools missing audit records: {missing_tool}")

        error_sources = {r.get("source") for r in rows if r.get("event") == "error"}
        missing_ev = [ev for ev in listener_events if ev not in error_sources]
        self.assertEqual(missing_ev, [], f"listeners missing error-path audit records: {missing_ev}")

        self.assertTrue(any(r.get("event") == "message_edit" for r in rows))
        self.assertTrue(any(r.get("event") == "api" and r.get("outcome") == "success" for r in rows))
        self.assertTrue(any(r.get("event") == "api" and r.get("outcome") == "error" for r in rows))
        self.assertTrue(any(r.get("event") == "block" for r in rows))

        # Exception inside a tool handler already produced outcome=error above.
        self.assertTrue(any(r.get("event") == "tool" and r.get("outcome") == "error" for r in rows))




if __name__ == "__main__":
    unittest.main()
