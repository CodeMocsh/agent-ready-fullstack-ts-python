"""The one rule that makes `.schema-entries.json` a gate rather than a mirror.

`make schema` regenerates the lock. Writing the current hashes wholesale would re-lock an
edited body on the way past, and `test_no_shipped_entry_body_has_changed` would never fire
again. Why the lock exists is in `docs/adr/0003-the-application-never-applies-ddl.md`.
"""

from devtools.schema import merged_lock


def test_a_key_already_locked_keeps_the_hash_it_was_locked_at() -> None:
    previous = {"0010_tasks": "the hash it shipped with"}
    current = {"0010_tasks": "the hash after somebody edited it", "0011_idx": "new entry"}

    assert merged_lock(previous, current) == {
        "0010_tasks": "the hash it shipped with",
        "0011_idx": "new entry",
    }


def test_an_entry_that_no_longer_exists_leaves_the_lock() -> None:
    previous = {"0010_tasks": "kept", "0090_deleted": "gone"}

    assert merged_lock(previous, {"0010_tasks": "kept"}) == {"0010_tasks": "kept"}
