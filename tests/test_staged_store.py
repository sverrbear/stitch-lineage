import pytest

from stitch_lineage.io.staged_store import (
    DESCRIPTIONS_FILENAME,
    STAGED_FILENAME,
    StagedDescription,
    StagedRelationship,
    StagedStoreError,
    add_staged,
    description_id,
    descriptions_path,
    drop_descriptions,
    drop_staged,
    read_descriptions,
    read_staged,
    relationship_id,
    remove_description,
    remove_staged,
    replace_staged,
    staged_path,
    upsert_description,
    write_descriptions,
    write_staged,
)


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


@pytest.fixture
def store(tmp_path):
    return staged_path(tmp_path / ".stitch")


def test_staged_path_sits_under_the_output_dir(tmp_path):
    assert staged_path(tmp_path / ".stitch") == tmp_path / ".stitch" / STAGED_FILENAME


def test_missing_file_is_an_empty_store_not_an_error(store):
    assert read_staged(store) == []


def test_round_trips_every_field(store):
    entry = _entry(cardinality="one-to-one", created_at="2026-08-09T12:00:00+00:00")
    write_staged([entry], store)
    (loaded,) = read_staged(store)
    assert loaded.model_dump() == entry.model_dump()


def test_write_creates_the_output_dir(store):
    write_staged([_entry()], store)
    assert store.is_file()


def test_id_is_derived_from_the_endpoints_and_is_case_insensitive():
    assert _entry().id == relationship_id(
        "fct_orders", "customer_id", "dim_customers", "customer_id"
    )
    assert _entry().id == _entry(from_model="FCT_ORDERS", from_column="CUSTOMER_ID").id


def test_id_read_from_the_file_is_never_trusted(store):
    store.parent.mkdir(parents=True)
    store.write_text(
        "relationships:\n"
        "  - id: deadbeef\n"
        "    from_model: fct_orders\n"
        "    from_column: customer_id\n"
        "    to_model: dim_customers\n"
        "    to_column: customer_id\n"
    )
    (loaded,) = read_staged(store)
    assert loaded.id == _entry().id


def test_different_endpoints_get_different_ids():
    assert _entry().id != _entry(to_model="dim_users").id


def test_ordering_is_deterministic_regardless_of_insertion_order(store, tmp_path):
    entries = [
        _entry(from_model="fct_orders", from_column="customer_id"),
        _entry(from_model="fct_events", from_column="user_id", to_model="dim_users"),
        _entry(from_model="fct_orders", from_column="store_id", to_model="dim_stores"),
    ]
    write_staged(entries, store)
    other = staged_path(tmp_path / "other")
    write_staged(list(reversed(entries)), other)
    assert store.read_text() == other.read_text()
    assert [e.from_column for e in read_staged(store)] == ["user_id", "customer_id", "store_id"]


def test_repeated_writes_of_the_same_set_are_byte_identical(store):
    write_staged([_entry()], store)
    first = store.read_text()
    write_staged(read_staged(store), store)
    assert store.read_text() == first


def test_write_dedupes_by_id_keeping_the_first(store):
    write_staged([_entry(cardinality="many-to-one"), _entry(cardinality="one-to-one")], store)
    (loaded,) = read_staged(store)
    assert loaded.cardinality == "many-to-one"


def test_the_file_carries_a_do_not_commit_header(store):
    write_staged([_entry()], store)
    assert "never commit" in store.read_text().splitlines()[1]


def test_add_staged_creates_then_dedupes(store):
    stored, created = add_staged(_entry(), store)
    assert created and stored.id == _entry().id

    stored, created = add_staged(_entry(cardinality="one-to-one"), store)
    assert not created
    # re-staging never rewrites an existing declaration
    assert stored.cardinality == "many-to-one"
    assert len(read_staged(store)) == 1


def test_add_staged_appends_a_second_relationship(store):
    add_staged(_entry(), store)
    add_staged(_entry(from_column="store_id", to_model="dim_stores"), store)
    assert len(read_staged(store)) == 2


def test_remove_staged_reports_whether_it_hit(store):
    add_staged(_entry(), store)
    assert remove_staged(_entry().id, store) is True
    assert read_staged(store) == []
    assert remove_staged(_entry().id, store) is False


def test_drop_staged_clears_only_the_named_ids(store):
    keep = _entry(from_column="store_id", to_model="dim_stores")
    write_staged([_entry(), keep], store)
    assert drop_staged({_entry().id}, store) == 1
    assert [e.id for e in read_staged(store)] == [keep.id]


def test_drop_staged_of_nothing_leaves_the_file_untouched(store):
    write_staged([_entry()], store)
    before = store.read_text()
    assert drop_staged({"nope"}, store) == 0
    assert store.read_text() == before


def test_an_empty_store_writes_an_empty_list(store):
    write_staged([], store)
    assert read_staged(store) == []


def test_writes_leave_no_temp_files_behind(store):
    write_staged([_entry()], store)
    assert [p.name for p in store.parent.iterdir()] == [STAGED_FILENAME]


def test_a_write_never_leaves_a_truncated_file(store, monkeypatch):
    """The replace is atomic: a crash mid-write leaves the previous content intact."""
    write_staged([_entry()], store)
    before = store.read_text()

    import stitch_lineage.io.staged_store as module

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module.os, "replace", boom)
    with pytest.raises(OSError):
        write_staged([_entry(), _entry(from_column="store_id", to_model="dim_stores")], store)
    assert store.read_text() == before
    assert [p.name for p in store.parent.iterdir()] == [STAGED_FILENAME]


def test_an_empty_file_reads_as_an_empty_store(store):
    store.parent.mkdir(parents=True)
    store.write_text("")
    assert read_staged(store) == []


def test_unparseable_yaml_names_the_fix(store):
    store.parent.mkdir(parents=True)
    store.write_text("relationships: [unclosed\n")
    with pytest.raises(StagedStoreError, match="delete the file"):
        read_staged(store)


def test_a_non_mapping_document_is_rejected(store):
    store.parent.mkdir(parents=True)
    store.write_text("- just a list\n")
    with pytest.raises(StagedStoreError, match="must contain a YAML mapping"):
        read_staged(store)


def test_a_relationship_missing_its_endpoints_is_rejected(store):
    store.parent.mkdir(parents=True)
    store.write_text("relationships:\n  - from_model: fct_orders\n")
    with pytest.raises(StagedStoreError, match="invalid relationship"):
        read_staged(store)


# --- the description store (issue #70) --------------------------------------------------


@pytest.fixture
def descriptions(tmp_path):
    return descriptions_path(tmp_path / ".stitch")


def _description(entity="fct_orders", column="customer_id", text="Who placed the order", **kwargs):
    return StagedDescription(entity=entity, column=column, new_description=text, **kwargs)


def test_descriptions_path_sits_under_the_output_dir(tmp_path):
    assert descriptions_path(tmp_path / ".stitch") == tmp_path / ".stitch" / DESCRIPTIONS_FILENAME


def test_a_missing_description_store_reads_as_empty(descriptions):
    assert read_descriptions(descriptions) == []


def test_the_id_is_derived_from_the_target_only(descriptions):
    assert _description(text="one").id == _description(text="two").id
    assert _description(column=None).id != _description().id
    assert _description(entity="dim_customers").id != _description().id


def test_a_description_id_can_never_collide_with_a_relationship_id():
    # different namespaces: the two stores are cleared independently at apply time
    assert description_id("fct_orders", "customer_id") != relationship_id(
        "fct_orders", "customer_id", "fct_orders", "customer_id"
    )


def test_upsert_creates_then_replaces(descriptions):
    stored, created = upsert_description(_description(text="first"), descriptions)
    assert created is True
    assert stored.new_description == "first"

    stored, created = upsert_description(_description(text="second"), descriptions)
    assert created is False
    entries = read_descriptions(descriptions)
    assert len(entries) == 1
    assert entries[0].new_description == "second"


def test_a_model_level_edit_and_a_column_edit_coexist(descriptions):
    upsert_description(_description(column=None, text="The orders fact"), descriptions)
    upsert_description(_description(text="Who placed it"), descriptions)
    assert len(read_descriptions(descriptions)) == 2


def test_descriptions_round_trip_with_a_multi_line_body(descriptions):
    text = "Gross total in USD.\nExcludes tax.\n"
    upsert_description(_description(text=text), descriptions)
    assert read_descriptions(descriptions)[0].new_description == text


def test_description_writes_are_byte_identical_regardless_of_order(tmp_path):
    first = tmp_path / "a.yml"
    second = tmp_path / "b.yml"
    entries = [_description(), _description(column=None), _description(entity="dim_customers")]
    write_descriptions(entries, first)
    write_descriptions(list(reversed(entries)), second)
    assert first.read_bytes() == second.read_bytes()


def test_remove_and_drop_descriptions(descriptions):
    entry = _description()
    upsert_description(entry, descriptions)
    assert remove_description("nosuchid", descriptions) is False
    assert remove_description(entry.id, descriptions) is True
    assert read_descriptions(descriptions) == []

    upsert_description(entry, descriptions)
    assert drop_descriptions({entry.id, "nosuchid"}, descriptions) == 1
    assert read_descriptions(descriptions) == []


def test_a_corrupt_description_store_names_the_fix(descriptions):
    descriptions.parent.mkdir(parents=True)
    descriptions.write_text("descriptions: [unclosed\n")
    with pytest.raises(StagedStoreError, match="delete the file"):
        read_descriptions(descriptions)


def test_a_description_without_text_is_rejected(descriptions):
    descriptions.parent.mkdir(parents=True)
    descriptions.write_text("descriptions:\n  - entity: fct_orders\n")
    with pytest.raises(StagedStoreError, match="invalid description"):
        read_descriptions(descriptions)


# --- editing a staged relationship (issue #71) ------------------------------------------


def test_replace_keeps_the_id_when_only_the_cardinality_changes(store):
    original = _entry(created_at="2026-08-01T00:00:00+00:00")
    add_staged(original, store)

    stored, moved = replace_staged(original.id, _entry(cardinality="one-to-one"), store)
    assert moved is False
    assert stored.cardinality == "one-to-one"
    # an edit is not a re-staging: when it was drawn survives the edit
    assert stored.created_at == "2026-08-01T00:00:00+00:00"
    assert [entry.cardinality for entry in read_staged(store)] == ["one-to-one"]


def test_replace_rehashes_the_id_when_endpoints_change(store):
    original = _entry()
    add_staged(original, store)
    edited = _entry(to_model="dim_users", to_column="user_id")

    stored, moved = replace_staged(original.id, edited, store)
    assert moved is True
    assert stored.id == edited.id
    assert [entry.id for entry in read_staged(store)] == [edited.id]


def test_replace_collapses_into_an_existing_pair_instead_of_duplicating(store):
    first = _entry()
    second = _entry(from_column="order_total")
    add_staged(first, store)
    add_staged(second, store)

    # editing `second` onto `first`'s endpoints: one entry survives, and it is the one
    # already staged (its cardinality is not silently overwritten)
    stored, moved = replace_staged(second.id, _entry(cardinality="one-to-one"), store)
    assert moved is True
    assert stored.id == first.id
    assert stored.cardinality == "many-to-one"
    assert [entry.id for entry in read_staged(store)] == [first.id]


def test_replace_of_an_unknown_id_is_none(store):
    add_staged(_entry(), store)
    assert replace_staged("nosuchid", _entry(cardinality="one-to-one"), store) is None
    assert len(read_staged(store)) == 1
