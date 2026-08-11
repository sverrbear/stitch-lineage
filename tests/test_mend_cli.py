"""`stitch mend` and `doctor --write-access` end to end, plus the config and plan store.

Every test here runs offline. The one command that must talk to Metabase (`mend --apply`)
is exercised by monkeypatching the client factory: there is no code path in this file that
can reach a real instance.
"""

import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import mend_scenario as scenario
import pytest
from conftest import plain
from test_mend_apply import FakeMetabase, plan_of, strip_card
from typer.testing import CliRunner

from stitch_lineage import cli
from stitch_lineage.cli import app
from stitch_lineage.config import MendConfig, StitchConfigError, load_config
from stitch_lineage.io.graph_store import previous_graph_path, write_graph
from stitch_lineage.io.mend_store import MendStoreError, plan_path, read_plan, write_plan
from stitch_lineage.mend.models import CardPlan, MendAction, MendPlan

runner = CliRunner()

CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
"""

MEND_CONFIG = (
    CONFIG
    + """
mend:
  slack_webhook: ${STITCH_SLACK_WEBHOOK_URL}
  auto: [repoint, archive]
  notify_only_collections: ["*Personal*", "Sandbox"]
"""
)


def _project(tmp_path: Path, config: str = CONFIG) -> Path:
    """A repo with a stitch.yml, both graphs, and a cached Metabase payload."""
    (tmp_path / "stitch.yml").write_text(config)
    graph_path = tmp_path / ".stitch" / "graph.json"
    write_graph(scenario.candidate_graph(), graph_path)
    write_graph(scenario.baseline_graph(), previous_graph_path(graph_path))
    run_dir = tmp_path / ".stitch" / "cache" / "2026-08-11T09-00-00-000000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "payload.json").write_text(
        json.dumps(scenario.payload().model_dump(mode="json")), encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


def test_mend_defaults_apply_all_three_actions_and_protect_personal_collections():
    mend = MendConfig()
    assert mend.auto == ["repoint", "strip", "archive"]
    assert mend.notify_only_collections == ["*Personal*"]
    assert mend.slack_webhook is None


def test_the_mend_section_is_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "key")
    monkeypatch.setenv("STITCH_SLACK_WEBHOOK_URL", "https://hooks.example.com/T/B/x")
    path = tmp_path / "stitch.yml"
    path.write_text(MEND_CONFIG)
    cfg = load_config(path)
    assert cfg.mend.auto == ["repoint", "archive"]
    assert cfg.mend.notify_only_collections == ["*Personal*", "Sandbox"]
    assert cfg.mend.slack_webhook == "https://hooks.example.com/T/B/x"
    cfg.mend.require_env()


def test_an_unset_webhook_variable_is_not_a_load_error_but_blocks_posting(tmp_path, monkeypatch):
    monkeypatch.delenv("STITCH_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "key")
    path = tmp_path / "stitch.yml"
    path.write_text(MEND_CONFIG)
    cfg = load_config(path)
    assert cfg.mend.missing_env == ["STITCH_SLACK_WEBHOOK_URL"]
    with pytest.raises(StitchConfigError, match="STITCH_SLACK_WEBHOOK_URL"):
        cfg.mend.require_env()


def test_a_literal_webhook_url_is_refused(tmp_path):
    path = tmp_path / "stitch.yml"
    path.write_text(CONFIG + '\nmend:\n  slack_webhook: "https://hooks.slack.com/services/T/B/x"\n')
    with pytest.raises(StitchConfigError, match="environment variable reference"):
        load_config(path)


@pytest.mark.parametrize("bad", ["notify", "repoints", "delete"])
def test_mend_auto_rejects_anything_that_is_not_an_action(tmp_path, bad):
    path = tmp_path / "stitch.yml"
    path.write_text(CONFIG + f"\nmend:\n  auto: [{bad}]\n")
    with pytest.raises(StitchConfigError):
        load_config(path)


def test_mend_auto_deduplicates():
    assert MendConfig(auto=["strip", "strip", "repoint"]).auto == ["strip", "repoint"]


# --------------------------------------------------------------------------------------
# the plan store
# --------------------------------------------------------------------------------------


def test_a_plan_round_trips_through_the_store(tmp_path):
    plan = plan_of(strip_card())
    path = write_plan(plan, plan_path(tmp_path / ".stitch"))
    assert path.name == "mend_plan.json"
    assert read_plan(path) == plan


def test_the_stored_plan_is_byte_stable(tmp_path):
    plan = plan_of(strip_card())
    first = write_plan(plan, tmp_path / "a.json").read_text()
    second = write_plan(read_plan(tmp_path / "a.json"), tmp_path / "b.json").read_text()
    assert first == second
    assert first.endswith("\n")


def test_a_missing_plan_names_the_fix(tmp_path):
    with pytest.raises(MendStoreError, match="run 'stitch mend --plan' first"):
        read_plan(tmp_path / "nope.json")


def test_an_unusable_plan_names_the_fix(tmp_path):
    path = tmp_path / "mend_plan.json"
    path.write_text("{not json")
    with pytest.raises(MendStoreError, match="re-run 'stitch mend --plan'"):
        read_plan(path)


# --------------------------------------------------------------------------------------
# impact --format json
# --------------------------------------------------------------------------------------


def test_impact_format_json_is_machine_readable(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["impact", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["removed"] == [scenario.AMOUNT, scenario.PROMO]
    by_label = {column["label"]: column for column in payload["columns"]}
    promo = by_label["fct_orders.promo_code"]
    assert promo["change"] == "removed"
    assert [card["card_id"] for card in promo["cards"]] == [402, 403, 404]
    assert promo["cards"][0]["name"] == scenario.CARD_NAMES[402]
    assert payload["card_count"] == 6


def test_impact_format_json_is_empty_when_nothing_changed(tmp_path, monkeypatch):
    project = _project(tmp_path)
    write_graph(scenario.candidate_graph(), previous_graph_path(project / ".stitch" / "graph.json"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["impact", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["columns"] == []


def test_impact_rejects_an_unknown_format(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["impact", "--format", "yaml"])
    assert result.exit_code == 1
    assert "unsupported --format" in plain(result.output)


# --------------------------------------------------------------------------------------
# mend --plan, entirely offline
# --------------------------------------------------------------------------------------


def test_mend_plan_writes_a_plan_and_renders_it(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    result = runner.invoke(
        app,
        [
            "mend",
            "--plan",
            "--use-cache",
            "--rename",
            "fct_orders.amount=fct_orders.amount_usd",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "STRIP (2)" in plain(result.output)
    assert "fct_orders.promo_code" in plain(result.output)

    plan = read_plan(project / ".stitch" / "mend_plan.json")
    assert {card.card_id: card.action for card in plan.cards} == {
        401: MendAction.REPOINT,
        402: MendAction.STRIP,
        403: MendAction.ARCHIVE,
        404: MendAction.STRIP,
        405: MendAction.NOTIFY,
        406: MendAction.NOTIFY,
    }
    assert plan.renames == {"fct_orders.amount": "fct_orders.amount_usd"}


def test_mend_plan_honours_the_configured_autonomy(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "key")
    monkeypatch.setenv("STITCH_SLACK_WEBHOOK_URL", "https://hooks.example.com/T/B/x")
    project = _project(tmp_path, MEND_CONFIG)  # auto: [repoint, archive] -- no strip
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--plan", "--use-cache"])
    assert result.exit_code == 0, result.output
    plan = read_plan(project / ".stitch" / "mend_plan.json")
    downgraded = {card.card_id: card.downgraded_from for card in plan.cards}
    assert downgraded[402] is MendAction.STRIP
    assert downgraded[404] is MendAction.STRIP


@pytest.mark.parametrize("output_format", ["slack", "github-comment", "text"])
def test_mend_plan_renders_every_format(tmp_path, monkeypatch, output_format):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["mend", "--plan", "--use-cache", "--format", output_format])
    assert result.exit_code == 0, result.output
    assert "stitch mend" in plain(result.output)


def test_mend_plan_without_a_cached_payload_says_what_to_run(tmp_path, monkeypatch):
    (tmp_path / "stitch.yml").write_text(CONFIG)
    graph_path = tmp_path / ".stitch" / "graph.json"
    write_graph(scenario.candidate_graph(), graph_path)
    write_graph(scenario.baseline_graph(), previous_graph_path(graph_path))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["mend", "--plan", "--use-cache"])
    assert result.exit_code == 1
    assert "no cached Metabase payload" in plain(result.output)


@pytest.mark.parametrize(
    "bad_rename", ["fct_orders.amount", "=fct_orders.amount_usd", "fct_orders.amount="]
)
def test_a_malformed_rename_is_refused(tmp_path, monkeypatch, bad_rename):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["mend", "--plan", "--use-cache", "--rename", bad_rename])
    assert result.exit_code == 1
    assert "--rename expects old=new" in plain(result.output)


def test_mend_needs_exactly_one_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    for args in (["mend"], ["mend", "--plan", "--apply"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 1
        assert "exactly one of --plan or --apply" in plain(result.output)


def test_a_plan_file_argument_without_apply_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["mend", "--plan", "some_plan.json"])
    assert result.exit_code == 1
    assert "only makes sense with --apply" in plain(result.output)


# --------------------------------------------------------------------------------------
# mend --apply, against a fake client
# --------------------------------------------------------------------------------------


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeMetabase()
    monkeypatch.setattr(cli, "_metabase_client", lambda cfg, cache_dir=None: client)
    return client


def test_mend_apply_writes_and_summarises(tmp_path, monkeypatch, fake_client):
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply"])
    assert result.exit_code == 0, result.output
    assert "APPLIED (1)" in plain(result.output)
    assert "#402 Orders, promo cohort" in plain(result.output)
    assert fake_client.writes


def test_mend_apply_exits_non_zero_when_a_card_fails(tmp_path, monkeypatch):
    client = FakeMetabase(query_results={402: {"status": "failed", "error": "boom"}})
    monkeypatch.setattr(cli, "_metabase_client", lambda cfg, cache_dir=None: client)
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply"])
    assert result.exit_code == 1
    assert "FAILED (1)" in plain(result.output)
    assert client.reverts


def test_mend_apply_takes_a_named_plan_file(tmp_path, monkeypatch, fake_client):
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), project / "custom_plan.json")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply", "custom_plan.json"])
    assert result.exit_code == 0, result.output
    assert fake_client.writes


def test_mend_apply_without_a_plan_names_the_fix(tmp_path, monkeypatch, fake_client):
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["mend", "--apply"])
    assert result.exit_code == 1
    assert "run 'stitch mend --plan' first" in plain(result.output)


def test_mend_apply_announces_force(tmp_path, monkeypatch, fake_client):
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply", "--force"])
    assert result.exit_code == 0, result.output
    assert "the staleness guard is off" in plain(result.output)


def test_a_notify_only_plan_writes_nothing(tmp_path, monkeypatch, fake_client):
    project = _project(tmp_path)
    plan = MendPlan(
        cards=[CardPlan(card_id=405, name="Scratch", action=MendAction.NOTIFY, reason="personal")]
    )
    write_plan(plan, plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply"])
    assert result.exit_code == 0, result.output
    assert "nothing to write" in plain(result.output)
    assert fake_client.writes == []


def test_slack_posting_is_skipped_with_a_clear_reason_when_unconfigured(
    tmp_path, monkeypatch, fake_client
):
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply", "--slack"])
    assert result.exit_code == 0, result.output
    assert "no mend.slack_webhook configured" in plain(result.output)


def test_the_slack_notice_is_posted_through_the_webhook_client(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "key")
    monkeypatch.setenv("STITCH_SLACK_WEBHOOK_URL", "https://hooks.example.com/T/B/x")
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "post_message", lambda url, text: posted.append((url, text)))
    project = _project(tmp_path, MEND_CONFIG)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--plan", "--use-cache", "--slack"])
    assert result.exit_code == 0, result.output
    assert posted and posted[0][0] == "https://hooks.example.com/T/B/x"
    assert "stitch mend" in posted[0][1]
    assert "plan notice posted" in plain(result.output)


# --------------------------------------------------------------------------------------
# the loading state (issue #161): visible while you wait, invisible once redirected
# --------------------------------------------------------------------------------------

# a spinner that leaked into redirected output shows up as one of these: the cursor and
# repaint codes Rich writes, or the braille frames themselves
ANSI = "\x1b["
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _has_spinner_debris(text: str) -> bool:
    return ANSI in text or any(frame in text for frame in SPINNER_FRAMES)


@pytest.mark.parametrize("output_format", ["text", "slack", "github-comment"])
def test_a_redirected_plan_carries_no_spinner_debris(tmp_path, monkeypatch, output_format):
    """Nothing here is a terminal, so the progress display must render nothing at all --
    `--format github-comment` gets piped straight into a PR comment."""
    monkeypatch.chdir(_project(tmp_path))
    result = runner.invoke(app, ["mend", "--plan", "--use-cache", "--format", output_format])
    assert result.exit_code == 0, result.output
    assert "stitch mend" in result.stdout
    assert not _has_spinner_debris(result.stdout)
    assert not _has_spinner_debris(result.stderr)


def test_a_redirected_apply_carries_no_spinner_debris(tmp_path, monkeypatch, fake_client):
    project = _project(tmp_path)
    write_plan(plan_of(strip_card()), plan_path(project / ".stitch"))
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["mend", "--apply"])
    assert result.exit_code == 0, result.output
    assert "APPLIED (1)" in result.stdout
    assert not _has_spinner_debris(result.stdout)
    assert not _has_spinner_debris(result.stderr)


def _with_tty_stderr(code: str, cwd: Path) -> tuple[str, str]:
    """Run `code` in a child with stderr on a pty and stdout on a pipe, returning both.

    That pairing -- an interactive terminal, output redirected to a file -- is the case
    CliRunner cannot reach, because nothing it hands the process is a terminal, and it is
    the only case where a live display can eat the redirected payload.
    """
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color", "COLUMNS": "80"},
    )
    os.close(slave)
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:  # the child exited and closed the last slave fd
            break
        if not data:
            break
        chunks.append(data)
    os.close(master)
    stdout, _ = process.communicate(timeout=120)
    stderr = b"".join(chunks).decode(errors="replace")
    assert process.returncode == 0, stderr
    return stdout.decode(), stderr


needs_pty = pytest.mark.skipif(
    os.name != "posix", reason="needs a pty to fake an interactive terminal"
)

# what `mend --apply` does per card: write payload to stdout from inside a live stage
_ECHO_FROM_A_LIVE_STAGE = """
from stitch_lineage.cli import _live_progress, _paused_echo

with _live_progress() as progress:
    progress.add_task("applying cards", total=2)
    echo = _paused_echo(progress)
    echo("PAYLOAD LINE ONE")
    echo("PAYLOAD LINE TWO")
"""


@needs_pty
def test_stdout_written_during_a_live_stage_survives_being_redirected(tmp_path):
    """The regression that would make `mend --apply > log.txt` come out empty.

    _paused_echo tears the display down around each write, so the diffs reach stdout the
    way they always did -- clean in the file, and not stamped over the spinner on screen.
    """
    stdout, stderr = _with_tty_stderr(_ECHO_FROM_A_LIVE_STAGE, tmp_path)
    assert stdout == "PAYLOAD LINE ONE\nPAYLOAD LINE TWO\n"
    assert not _has_spinner_debris(stdout)
    # the terminal is where the waiting is shown, and this one really is a terminal
    assert _has_spinner_debris(stderr)


# no _paused_echo: what happens to a plain stdout write that forgets to pause the display
_UNPAUSED_STDOUT_WRITE = """
from stitch_lineage.cli import _live_progress

with _live_progress() as progress:
    progress.add_task("applying cards", total=1)
    print("PAYLOAD LINE ONE")
"""


@needs_pty
def test_a_live_stage_never_swallows_stdout_even_when_nobody_pauses_it(tmp_path):
    """Why _stage_progress turns Rich's redirect_stdout off.

    Left at its default, Rich replaces sys.stdout for the whole live region whenever its
    own console is a terminal -- so on a tty with stdout piped, this write would be
    rerouted to the terminal and the redirect would receive nothing at all. Losing
    payload silently is far worse than a spinner someone forgot to pause, so the
    redirection is off and the worst case is cosmetic.
    """
    stdout, _stderr = _with_tty_stderr(_UNPAUSED_STDOUT_WRITE, tmp_path)
    assert "PAYLOAD LINE ONE" in stdout


@needs_pty
def test_the_plan_still_reaches_a_redirected_stdout_while_the_terminal_shows_progress(tmp_path):
    """`stitch mend --plan > plan.txt` from a terminal: the file gets the whole plan, and
    the progress display stays behind on stderr where it cannot corrupt it."""
    code = "from stitch_lineage.cli import app; app()"
    stdout, stderr = _with_tty_stderr(
        f"import sys; sys.argv[1:] = ['mend', '--plan', '--use-cache']; {code}",
        _project(tmp_path),
    )
    assert "stitch mend" in stdout
    assert "fct_orders.promo_code" in stdout
    assert not _has_spinner_debris(stdout)
    assert _has_spinner_debris(stderr)


# --------------------------------------------------------------------------------------
# doctor --write-access
# --------------------------------------------------------------------------------------


class WriteProbeClient(FakeMetabase):
    def __init__(self, cards: list[dict], user: dict | None = None, revisions: bool = True) -> None:
        super().__init__()
        self._cards = cards
        self._user = user or {"common_name": "CI bot", "is_superuser": False}
        self._revisions = revisions

    def current_user(self) -> dict:
        return self._user

    def list_cards(self) -> list[dict]:
        return self._cards

    def card_revisions(self, card_id: int) -> list[dict]:
        return [{"id": 1}] if self._revisions else []


def _run_doctor(tmp_path, monkeypatch, client, config: str = CONFIG):
    monkeypatch.setattr(cli, "_metabase_client", lambda cfg, cache_dir=None: client)
    (tmp_path / "stitch.yml").write_text(config)
    monkeypatch.chdir(tmp_path)
    return runner.invoke(app, ["doctor", "--write-access"])


def test_write_access_reports_identity_writability_and_the_autonomy_dial(tmp_path, monkeypatch):
    client = WriteProbeClient([{"id": 1, "can_write": True}, {"id": 2, "can_write": False}])
    result = _run_doctor(tmp_path, monkeypatch, client)
    assert result.exit_code == 0, result.output
    assert "authenticates as CI bot" in plain(result.output)
    assert "1/2 cards report can_write" in plain(result.output)
    assert "1 card(s) are read-only" in plain(result.output)
    assert "revision history readable" in plain(result.output)
    assert "mend.auto: repoint, strip, archive" in plain(result.output)
    assert "notice and summary print locally only" in plain(result.output)


def test_write_access_fails_when_nothing_is_writable(tmp_path, monkeypatch):
    client = WriteProbeClient([{"id": 1, "can_write": False}])
    result = _run_doctor(tmp_path, monkeypatch, client)
    assert result.exit_code == 1
    assert "no card is writable" in plain(result.output)


def test_write_access_degrades_when_the_version_is_silent(tmp_path, monkeypatch):
    client = WriteProbeClient([{"id": 1}], revisions=False)
    result = _run_doctor(tmp_path, monkeypatch, client)
    assert result.exit_code == 0, result.output
    assert "do not report can_write" in plain(result.output)
    assert "revision history not readable" in plain(result.output)


def test_write_access_flags_a_webhook_variable_that_is_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("STITCH_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "key")
    client = WriteProbeClient([{"id": 1, "can_write": True}])
    result = _run_doctor(tmp_path, monkeypatch, client, MEND_CONFIG)
    assert result.exit_code == 1
    assert "STITCH_SLACK_WEBHOOK_URL" in plain(result.output)


def test_write_access_writes_nothing(tmp_path, monkeypatch):
    client = WriteProbeClient([{"id": 1, "can_write": True}])
    _run_doctor(tmp_path, monkeypatch, client)
    assert client.writes == []
    assert client.dashboard_writes == []
    assert client.reverts == []
