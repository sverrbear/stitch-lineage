"""The round-trip writer (SPEC.md section 8.2).

The load-bearing test here is `test_the_edit_is_insert_only`: a `stitch apply` diff must
contain the inserted declaration and nothing else. A PR that reformats a hand-maintained
schema file gets the tool banned, so the guarantee is asserted structurally (difflib
opcodes) rather than by eyeballing a golden string.
"""

import difflib
import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from stitch_lineage.config import RelationshipsConfig
from stitch_lineage.io.staged_store import StagedDescription, StagedRelationship
from stitch_lineage.write.yaml_writer import apply_plan, model_writeability, plan_writes

FIXTURES = Path(__file__).parent / "fixtures" / "dbt_repo"
MARTS = "models/marts/_schema.yml"
EVENTS = "models/events/_schema.yml"


def _node(name, schema="marts", patch=MARTS):
    return {
        "resource_type": "model",
        "name": name,
        "schema": schema,
        "patch_path": f"demo://{patch}" if patch else None,
    }


MANIFEST = {
    "nodes": {
        "model.demo.fct_orders": _node("fct_orders"),
        "model.demo.dim_customers": _node("dim_customers"),
        "model.demo.fct_events": _node("fct_events", "events", EVENTS),
        "model.demo.dim_users": _node("dim_users", "events", EVENTS),
        # in the manifest but with no YAML of its own -- stitch must not invent one
        "model.demo.dim_stores": _node("dim_stores", patch=None),
        # patch_path points at a file that is not in the repo
        "model.demo.dim_regions": _node("dim_regions", patch="models/gone/_schema.yml"),
        "seed.demo.country_codes": {"resource_type": "seed", "name": "country_codes"},
    }
}


@pytest.fixture
def repo(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "repo")
    return tmp_path / "repo"


def _entry(
    from_model="fct_orders",
    from_column="customer_id",
    to_model="dim_customers",
    to_column="customer_id",
    **kwargs,
):
    return StagedRelationship(
        from_model=from_model,
        from_column=from_column,
        to_model=to_model,
        to_column=to_column,
        **kwargs,
    )


def _plan(repo, entries, write_to="relationships_test", **config):
    return plan_writes(entries, MANIFEST, repo, RelationshipsConfig(write_to=write_to, **config))


def _added(edit):
    return [
        line[1:]
        for line in edit.diff().splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


# --- the non-negotiable guarantee ------------------------------------------------------


def test_the_edit_is_insert_only(repo):
    """Nothing is removed or rewritten -- every original line survives, in order."""
    (edit,) = _plan(repo, [_entry()]).edits
    opcodes = difflib.SequenceMatcher(
        None, edit.original.splitlines(), edit.updated.splitlines()
    ).get_opcodes()
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}


def test_comments_odd_quoting_and_block_scalars_all_survive(repo):
    (edit,) = _plan(repo, [_entry()]).edits
    for fragment in (
        "# grain: one row per order line. Owned by the revenue pod.",
        "# FK -- declared below, no test yet",
        "tags: ['core', \"revenue\"]",
        "description: 'Who placed the order'",
        "description: >",
        "description: |",
        "data_tests: [not_null]",
        'description: "Customer dimension (SCD1)"',
    ):
        assert fragment in edit.updated, fragment


def test_a_file_with_no_staged_entry_is_never_touched(repo):
    plan = _plan(repo, [_entry()])
    assert [edit.path.name for edit in plan.edits] == ["_schema.yml"]
    assert plan.edits[0].path.parent.name == "marts"
    assert (repo / EVENTS).read_text() == (FIXTURES / EVENTS).read_text()


def test_applying_twice_is_a_no_op(repo):
    apply_plan(_plan(repo, [_entry()]).edits)
    after_first = (repo / MARTS).read_text()

    second = _plan(repo, [_entry()])
    assert second.edits == []
    assert [result.status for result in second.results] == ["unchanged"]
    assert (repo / MARTS).read_text() == after_first


# --- relationships_test form -----------------------------------------------------------


def test_writes_a_relationships_test_on_the_fk_column(repo):
    (edit,) = _plan(repo, [_entry()]).edits
    assert _added(edit) == [
        "        data_tests:",
        "          - relationships:",
        "              to: ref('dim_customers')",
        "              field: customer_id",
        "              config:",
        "                severity: warn",
    ]


def test_the_test_lands_on_the_declaring_column(repo):
    (edit,) = _plan(repo, [_entry()]).edits
    lines = edit.updated.splitlines()
    fk = lines.index("      - name: customer_id   # FK -- declared below, no test yet")
    total = lines.index("      - name: order_total")
    assert fk < lines.index("              to: ref('dim_customers')") < total


def test_severity_is_configurable(repo):
    (edit,) = _plan(repo, [_entry()], validated_test_severity="error").edits
    assert "                severity: error" in _added(edit)


def test_no_severity_config_writes_a_bare_test(repo):
    (edit,) = _plan(repo, [_entry()], validated_test_severity="").edits
    assert _added(edit) == [
        "        data_tests:",
        "          - relationships:",
        "              to: ref('dim_customers')",
        "              field: customer_id",
    ]


def test_an_existing_tests_list_is_appended_to_not_replaced(repo):
    (edit,) = _plan(repo, [_entry(from_column="order_id")]).edits
    assert "          - unique" in edit.updated
    assert "          - not_null" in edit.updated
    assert "          - relationships:" in edit.updated


def test_the_files_own_tests_key_is_reused(repo):
    """The events file predates dbt 1.8 and says `tests:` -- do not mix in `data_tests:`."""
    entry = _entry(from_model="fct_events", from_column="user_id", to_model="dim_users")
    (edit,) = _plan(repo, [entry]).edits
    assert "    tests:" in _added(edit)
    assert not any("data_tests" in line for line in _added(edit))


def test_the_flush_dash_convention_round_trips(repo):
    entry = _entry(from_model="fct_events", from_column="user_id", to_model="dim_users")
    (edit,) = _plan(repo, [entry]).edits
    opcodes = difflib.SequenceMatcher(
        None, edit.original.splitlines(), edit.updated.splitlines()
    ).get_opcodes()
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}
    assert "- name: user_id" in edit.updated


def test_a_hand_written_test_is_detected_however_the_ref_is_spelled(repo):
    """A relationship someone already wrote by hand is never duplicated."""
    target = repo / MARTS
    target.write_text(
        target.read_text().replace(
            "        description: 'Who placed the order'",
            "        description: 'Who placed the order'\n"
            "        data_tests:\n"
            "          - relationships:\n"
            '              to: ref( "demo", "dim_customers" )\n'
            "              field: customer_id\n",
        )
    )
    plan = _plan(repo, [_entry()])
    assert plan.edits == []
    assert [result.status for result in plan.results] == ["unchanged"]


# --- meta form -------------------------------------------------------------------------


def test_meta_form_writes_the_dbt_metabase_interop_keys(repo):
    (edit,) = _plan(repo, [_entry()], write_to="meta").edits
    assert _added(edit) == [
        "        config:",
        "          meta:",
        "            metabase.fk_target_table: marts.dim_customers",
        "            metabase.fk_target_field: customer_id",
        "            relationship_type: many-to-one",
    ]


def test_meta_form_records_the_drawn_cardinality(repo):
    (edit,) = _plan(repo, [_entry(cardinality="one-to-one")], write_to="meta").edits
    assert "            relationship_type: one-to-one" in _added(edit)


def test_meta_form_is_insert_only_too(repo):
    (edit,) = _plan(repo, [_entry()], write_to="meta").edits
    opcodes = difflib.SequenceMatcher(
        None, edit.original.splitlines(), edit.updated.splitlines()
    ).get_opcodes()
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}


def test_meta_form_refuses_to_overwrite_a_conflicting_declaration(repo):
    apply_plan(_plan(repo, [_entry()], write_to="meta").edits)
    conflicting = _entry(to_model="dim_users")
    plan = _plan(repo, [conflicting], write_to="meta")
    assert plan.edits == []
    assert "already declares" in plan.failures[0].message


def test_contract_constraint_is_not_implemented(repo):
    with pytest.raises(NotImplementedError, match="contract_constraint"):
        _plan(repo, [_entry()], write_to="contract_constraint")


# --- inserting a column that has no YAML entry -----------------------------------------


def test_a_column_with_no_yaml_entry_is_inserted(repo):
    (edit,) = _plan(repo, [_entry(from_column="store_id", to_model="dim_customers")]).edits
    added = _added(edit)
    assert "      - name: store_id" in added
    assert "          - relationships:" in added


def test_an_inserted_column_lands_inside_its_own_model(repo):
    (edit,) = _plan(repo, [_entry(from_column="store_id")]).edits
    lines = edit.updated.splitlines()
    assert lines.index("      - name: store_id") < lines.index("  - name: dim_customers")


def test_inserting_a_column_is_still_insert_only(repo):
    (edit,) = _plan(repo, [_entry(from_column="store_id")]).edits
    opcodes = difflib.SequenceMatcher(
        None, edit.original.splitlines(), edit.updated.splitlines()
    ).get_opcodes()
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}


# --- refusals ---------------------------------------------------------------------------


def test_a_model_with_no_schema_file_is_unappliable(repo):
    plan = _plan(repo, [_entry(from_model="dim_stores", from_column="region_id")])
    assert plan.edits == []
    (failure,) = plan.failures
    assert "has no schema YAML file" in failure.message
    assert "stitch does not invent" in failure.message


def test_a_patch_path_pointing_nowhere_is_unappliable(repo):
    plan = _plan(repo, [_entry(from_model="dim_regions", from_column="country_id")])
    assert plan.edits == []
    assert "does not exist" in plan.failures[0].message


def test_an_unknown_source_model_is_unappliable(repo):
    plan = _plan(repo, [_entry(from_model="fct_ghost")])
    assert "not in the manifest" in plan.failures[0].message


def test_an_unknown_target_model_is_unappliable(repo):
    plan = _plan(repo, [_entry(to_model="dim_ghost")])
    assert plan.edits == []
    assert "not in the manifest" in plan.failures[0].message


def test_a_model_absent_from_its_schema_file_is_unappliable(repo):
    """dim_users is in the manifest and shares the events file, but has no models: entry."""
    plan = _plan(repo, [_entry(from_model="dim_users", from_column="user_id")])
    assert plan.edits == []
    assert "has no entry in its schema file" in plan.failures[0].message


def test_a_failure_does_not_block_the_other_entries(repo):
    plan = _plan(repo, [_entry(from_model="fct_ghost"), _entry()])
    assert len(plan.edits) == 1
    assert len(plan.failures) == 1
    assert len(plan.planned) == 1


def test_a_seed_is_not_a_relationship_target(repo):
    plan = _plan(repo, [_entry(to_model="country_codes")])
    assert "not in the manifest" in plan.failures[0].message


# --- planning and applying --------------------------------------------------------------


def test_several_entries_in_one_file_accumulate_into_one_edit(repo):
    plan = _plan(
        repo,
        [_entry(), _entry(from_column="order_id", to_column="customer_id")],
    )
    assert len(plan.edits) == 1
    assert len(plan.planned) == 2
    assert plan.edits[0].updated.count("- relationships:") == 2


def test_entries_in_different_files_produce_one_edit_each(repo):
    plan = _plan(
        repo,
        [_entry(), _entry(from_model="fct_events", from_column="user_id", to_model="dim_users")],
    )
    assert {edit.path.parent.name for edit in plan.edits} == {"marts", "events"}


def test_planning_never_touches_disk(repo):
    before = (repo / MARTS).read_text()
    _plan(repo, [_entry()])
    assert (repo / MARTS).read_text() == before


def test_apply_plan_writes_and_reports_the_paths(repo):
    plan = _plan(repo, [_entry()])
    written = apply_plan(plan.edits)
    assert written == [repo / MARTS]
    assert (repo / MARTS).read_text() == plan.edits[0].updated


def test_apply_plan_leaves_no_temp_files(repo):
    apply_plan(_plan(repo, [_entry()]).edits)
    assert sorted(p.name for p in (repo / MARTS).parent.iterdir()) == ["_schema.yml"]


def test_the_written_file_still_parses_as_dbt_yaml(repo):
    from ruamel.yaml import YAML

    apply_plan(_plan(repo, [_entry()]).edits)
    document = YAML(typ="safe").load((repo / MARTS).read_text())
    orders = next(m for m in document["models"] if m["name"] == "fct_orders")
    customer_id = next(c for c in orders["columns"] if c["name"] == "customer_id")
    assert customer_id["data_tests"][0]["relationships"] == {
        "to": "ref('dim_customers')",
        "field": "customer_id",
        "config": {"severity": "warn"},
    }


def test_ids_clear_only_for_the_files_that_were_written(repo):
    marts = _entry()
    events = _entry(from_model="fct_events", from_column="user_id", to_model="dim_users")
    plan = _plan(repo, [marts, events])
    cleared = plan.ids_for({repo / MARTS})
    assert cleared == {marts.id}


def test_already_declared_entries_clear_even_when_nothing_is_written(repo):
    apply_plan(_plan(repo, [_entry()]).edits)
    plan = _plan(repo, [_entry()])
    assert plan.ids_for(set()) == {_entry().id}


def test_the_diff_is_labelled_relative_to_the_project_root(repo):
    # the CLI passes a resolved root, so the label is repo-relative like a git diff
    diff = _plan(repo, [_entry()]).diff(repo.resolve())
    assert f"a/{MARTS}" in diff
    assert f"b/{MARTS}" in diff
    assert str(repo.resolve()) not in diff


def test_a_diff_outside_the_root_falls_back_to_the_full_path(repo):
    diff = _plan(repo, [_entry()]).diff(repo.parent / "elsewhere")
    assert MARTS in diff


def test_a_file_that_cannot_be_reproduced_is_refused_not_reformatted(repo):
    """A layout stitch cannot round-trip is reported, never silently rewritten."""
    target = repo / MARTS
    target.write_text(target.read_text().replace("version: 2", "version:    2"))
    before = target.read_text()
    plan = _plan(repo, [_entry()])
    assert plan.edits == []
    assert "cannot be edited without reformatting" in plan.failures[0].message
    assert target.read_text() == before


# --- indented blank lines inside a block scalar (#132) ----------------------------------
#
# The shape that refused every write into a 2156-line file on the real repo. A long
# `description: |` with paragraphs in it has blank lines BETWEEN those paragraphs, and
# an author (or an editor) writes them at the block's own indentation:
#
#     description: |
#       Orders, one row per line item.
#     ......                              <- six spaces, not an empty line
#       Rebuilt nightly by the core pipeline.
#
# YAML strips block indentation, so the string is identical either way -- but ruamel
# re-emits that line as truly empty, the pristine round trip came back differing in
# those bytes, and the ENTIRE file was declared unwritable. Two invisible lines cost
# every description edit and every relationship write-back in the file.

INDENTED_BLANK = """version: 2

models:
  - name: fct_orders
    description: |
      Orders, one row per line item.
{pad}
      Rebuilt nightly by the core pipeline.
    columns:
      - name: order_id
        description: "Primary key"
      - name: customer_id
        description: "Who placed the order"
"""

PADDED = INDENTED_BLANK.format(pad="      ")


def test_the_fixture_really_is_only_cosmetically_different(repo):
    """Guard the fixture itself: the two spellings must parse to the same document."""
    loader = YAML(typ="safe")
    assert loader.load(PADDED) == loader.load(INDENTED_BLANK.format(pad=""))
    assert "      \n" in PADDED
    assert len(PADDED.splitlines()) == len(INDENTED_BLANK.format(pad="").splitlines())


def test_an_indented_blank_in_a_block_scalar_no_longer_blocks_the_whole_file(repo):
    (repo / MARTS).write_text(PADDED)
    plan = _plan(repo, [_entry()])
    assert plan.failures == [], [result.message for result in plan.failures]
    assert len(plan.edits) == 1


def test_that_blank_line_comes_back_exactly_as_its_author_wrote_it(repo):
    """The rescue may not cost the guarantee: untouched lines stay byte-identical."""
    (repo / MARTS).write_text(PADDED)
    (edit,) = _plan(repo, [_entry()]).edits
    opcodes = difflib.SequenceMatcher(
        None, PADDED.splitlines(), edit.updated.splitlines(), autojunk=False
    ).get_opcodes()
    # insert-only, exactly as for a file that never needed the repair
    assert {tag for tag, *_ in opcodes} <= {"equal", "insert"}
    assert edit.updated.count("      \n") == PADDED.count("      \n")


def test_it_survives_the_write_itself(repo):
    target = repo / MARTS
    target.write_text(PADDED)
    apply_plan(_plan(repo, [_entry()]).edits)
    assert target.read_text().count("      \n") == PADDED.count("      \n")


def test_a_description_edit_into_such_a_file_removes_nothing_but_the_description(repo):
    """The exact case from #132: a description edit on a file with an indented blank."""
    (repo / MARTS).write_text(PADDED)
    (edit,) = _plan(repo, [_description(column="order_id", text="The order's primary key")]).edits
    removed = [
        line
        for line in edit.diff().splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert all(line[1:].strip() != "" for line in removed), removed


# --- files that genuinely cannot round-trip, and are declared so up front ---------------


MIXED_INDENT = """version: 2

models:
  - name: fct_orders
    description: "Orders"
    columns:
      - name: order_id
        description: "Primary key"
        data_tests:
        - unique
        - not_null
"""


def test_a_file_mixing_two_list_indentation_styles_still_refuses(repo):
    """ruamel has one indent setting per dump, so this shape cannot be reproduced."""
    (repo / MARTS).write_text(MIXED_INDENT)
    plan = _plan(repo, [_entry(from_column="order_id")])
    assert plan.edits == []
    assert "does not survive a round trip" in plan.failures[0].message


def test_the_refusal_names_the_remedy_for_the_change_that_asked(repo):
    """#132: a refused DESCRIPTION edit used to tell you to add the relationship by hand."""
    (repo / MARTS).write_text(MIXED_INDENT)
    relationship = _plan(repo, [_entry(from_column="order_id")]).failures[0].message
    description = _plan(repo, [_description(column=None, text="Orders")]).failures[0].message
    assert "add the relationship by hand" in relationship
    assert "relationship" not in description
    assert "description" in description


# --- descriptions (issue #70) -----------------------------------------------------------


def _description(entity="fct_orders", column="customer_id", text="Who placed it, FK to customers"):
    return StagedDescription(entity=entity, column=column, new_description=text)


def _loaded(edit):
    """The updated file parsed, so assertions read values instead of YAML quoting styles."""
    return YAML(typ="safe").load(edit.updated)


def _column_of(edit, model, column):
    models = {entry["name"]: entry for entry in _loaded(edit)["models"]}
    columns = {entry["name"]: entry for entry in models[model].get("columns") or []}
    return columns[column]


def _changed_regions(edit):
    original, updated = edit.original.splitlines(), edit.updated.splitlines()
    return [
        (tag, original[i1:i2], updated[j1:j2])
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, original, updated).get_opcodes()
        if tag != "equal"
    ]


def test_a_column_description_is_replaced_in_place(repo):
    (edit,) = _plan(repo, [_description(column="order_id", text="The order key")]).edits
    assert _column_of(edit, "fct_orders", "order_id")["description"] == "The order key"
    # its tests and comments are untouched: exactly one line differs
    (region,) = _changed_regions(edit)
    assert region[0] == "replace"
    assert all("description" in line for line in region[1] + region[2])


def test_a_missing_column_description_is_inserted_after_the_name(repo):
    """fct_events.event_id is in the YAML with a test but no description of its own."""
    entry = _description(entity="fct_events", column="event_id", text="One per emitted event")
    (edit,) = _plan(repo, [entry]).edits
    lines = edit.updated.splitlines()
    name_at = next(index for index, line in enumerate(lines) if "- name: event_id" in line)
    assert lines[name_at + 1].strip() == "description: One per emitted event"
    column = _column_of(edit, "fct_events", "event_id")
    assert column["description"] == "One per emitted event"
    # the test that was under it is still under it
    assert column["tests"] == ["unique"]


def test_a_model_description_is_written_on_the_model_entry(repo):
    entry = _description(entity="dim_customers", column=None, text="Every customer, one row each")
    (edit,) = _plan(repo, [entry]).edits
    models = {model["name"]: model for model in _loaded(edit)["models"]}
    assert models["dim_customers"]["description"] == "Every customer, one row each"
    assert len(_changed_regions(edit)) == 1


def test_a_column_absent_from_the_yaml_gets_an_entry_with_its_description(repo):
    (edit,) = _plan(repo, [_description(column="ordered_at", text="When it happened")]).edits
    assert _column_of(edit, "fct_orders", "ordered_at")["description"] == "When it happened"
    added = _added(edit)
    assert "      - name: ordered_at" in added


MULTILINE = "FK to dim_customers.\nNull for guest orders.\n"


def test_a_multi_line_description_is_written_as_a_block_scalar(repo):
    (edit,) = _plan(repo, [_description(column="customer_id", text=MULTILINE)]).edits
    assert "        description: |" in edit.updated
    assert "          FK to dim_customers." in edit.updated
    assert _column_of(edit, "fct_orders", "customer_id")["description"] == MULTILINE


def test_a_block_scalar_does_not_double_the_blank_line_after_it(repo):
    """The block ends with its own line break; the author's single blank line stays single."""
    (edit,) = _plan(repo, [_description(column="customer_id", text=MULTILINE)]).edits
    assert "\n\n\n" not in edit.updated
    assert len(_changed_regions(edit)) == 1


def test_a_multi_line_description_round_trips_through_the_repo(repo):
    apply_plan(_plan(repo, [_description(column="customer_id", text=MULTILINE)]).edits)
    # written, re-read, re-planned: nothing left to do
    replanned = _plan(repo, [_description(column="customer_id", text=MULTILINE)])
    assert replanned.edits == []
    assert replanned.results[0].status == "unchanged"


def test_a_description_that_already_matches_is_unchanged(repo):
    (result,) = _plan(repo, [_description(column="order_id", text="Primary key")]).results
    assert result.status == "unchanged"
    assert result.message == "the repo already has this description"


def test_an_existing_block_scalar_matches_whatever_its_trailing_newline(repo):
    # order_total's description is a `|` block in the fixture, so it loads with a trailing \n
    for text in ("Gross total in USD.\nExcludes tax.\n", "Gross total in USD.\nExcludes tax."):
        plan = _plan(repo, [_description(column="order_total", text=text)])
        assert plan.edits == []
        assert plan.results[0].status == "unchanged"


def test_a_description_on_a_model_with_no_schema_file_is_unappliable(repo):
    entry = _description(entity="dim_stores", column=None, text="Stores")
    (result,) = _plan(repo, [entry]).results
    assert result.status == "failed"
    assert "has no schema YAML file" in result.message


def test_a_description_on_an_unknown_model_is_unappliable(repo):
    (result,) = _plan(repo, [_description(entity="dim_ghost", column=None, text="Ghost")]).results
    assert result.status == "failed"
    assert "not in the manifest" in result.message


def test_a_relationship_and_a_description_in_one_file_make_one_edit(repo):
    changes = [_entry(), _description(column="customer_id", text="FK to dim_customers")]
    plan = _plan(repo, changes)
    assert len(plan.edits) == 1
    (edit,) = plan.edits
    assert any("relationships:" in line for line in _added(edit))
    assert _column_of(edit, "fct_orders", "customer_id")["description"] == "FK to dim_customers"
    assert [result.status for result in plan.results] == ["planned", "planned"]
    # and both clear from their stores together
    assert plan.ids_for({edit.path}) == {change.id for change in changes}


# --- write-ability, decided before anything is staged (#132) ----------------------------


def test_writeability_says_yes_for_a_file_stitch_can_reproduce(repo):
    entries = model_writeability(MANIFEST, repo)
    assert entries["fct_orders"].writable is True
    assert entries["fct_orders"].reason is None
    assert entries["fct_orders"].path == MARTS


def test_writeability_says_no_for_a_file_that_cannot_round_trip(repo):
    (repo / MARTS).write_text(MIXED_INDENT)
    entries = model_writeability(MANIFEST, repo)
    assert entries["fct_orders"].writable is False
    assert "does not survive a round trip" in entries["fct_orders"].reason
    # a model in a DIFFERENT file is unaffected -- the verdict is per file, not global
    assert entries["fct_events"].writable is True


def test_writeability_says_no_when_the_model_has_no_schema_file(repo):
    entry = model_writeability(MANIFEST, repo)["dim_stores"]
    assert entry.writable is False
    assert "no schema YAML file" in entry.reason


def test_writeability_says_no_when_the_schema_file_is_missing_from_the_repo(repo):
    entry = model_writeability(MANIFEST, repo)["dim_regions"]
    assert entry.writable is False
    assert "not in the repo" in entry.reason


def test_writeability_agrees_with_what_apply_actually_does(repo):
    """The promise the affordance rests on: offered == appliable, refused == refused."""
    for text, expected in ((PADDED, True), (MIXED_INDENT, False)):
        (repo / MARTS).write_text(text)
        offered = model_writeability(MANIFEST, repo)["fct_orders"].writable
        applied = _plan(repo, [_entry(from_column="order_id")]).failures == []
        assert offered is expected
        assert offered == applied


def test_writeability_proves_each_file_once(repo, monkeypatch):
    """Two models in one file must not cost two parses of it."""
    import stitch_lineage.write.yaml_writer as writer

    seen = []
    original = writer._file_refusal
    monkeypatch.setattr(
        writer, "_file_refusal", lambda path: (seen.append(path), original(path))[1]
    )
    model_writeability(MANIFEST, repo)
    assert len(seen) == len(set(seen))
