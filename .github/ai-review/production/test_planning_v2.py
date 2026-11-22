"""Provas offline do planejador v2. Nenhuma chamada de rede ou modelo."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import planning_v2 as v2
import contract_v2 as contract
import runner
from transport import Problem

BASE = "a" * 40
HEAD = "b" * 40


def fixture(path="src/example.py", *, new_lines=2, width=24):
    old = ["-" + "old_value = " + str(i) for i in range(new_lines)]
    new = ["+" + "new_value = " + str(i) + " " + "x" * width
           for i in range(new_lines)]
    return {
        "filename": path, "status": "modified",
        "patch": f"@@ -1,{new_lines} +1,{new_lines} @@\n" + "\n".join(old + new),
        "additions": new_lines, "deletions": new_lines,
    }


class GH:
    def __init__(self, files):
        self.files = files
        self.head = HEAD

    def compare(self, pinned):
        return {"base_commit": {"sha": BASE}, "files": copy.deepcopy(self.files)}

    def pr(self, number):
        return {
            "number": number, "state": "open", "draft": False, "title": "Synthetic",
            "changed_files": len(self.files), "body": "",
            "head": {"sha": self.head, "repo": {"full_name": runner.REPOSITORY}},
            "base": {"sha": BASE, "ref": "main",
                     "repo": {"full_name": runner.REPOSITORY}},
        }


def pinned(count):
    return runner.Snapshot(77, BASE, HEAD, "Synthetic", "", count)


class PlanningV2Tests(unittest.TestCase):
    def test_small_hunk_is_complete_and_anchor_is_stable(self):
        gh = GH([fixture()])
        plan = v2.build_plan(gh, pinned(1))
        self.assertEqual(plan.coverage["files_omitted"], 0)
        self.assertEqual(plan.coverage["chunks_planned"], 1)
        self.assertEqual(plan.added_total, 2)
        self.assertEqual(len(plan.anchors), 2)
        first = plan.chunks[0].anchors[0]
        self.assertEqual(first[2], 1)
        self.assertTrue(first[0].startswith("a"))
        self.assertEqual(len(first[3]), 64)
        self.assertEqual(plan.digest, v2.build_plan(gh, pinned(1)).digest)
        with self.assertRaises(TypeError):
            plan.anchors["forged"] = first

    def test_15_files_over_80kib_are_all_included_in_multiple_chunks(self):
        files = [fixture(f"src/feature_{i:02}.py", new_lines=35, width=160)
                 for i in range(14)]
        files.append(fixture("tests/unit/test_encerramento_gui.py",
                             new_lines=35, width=160))
        size = sum(len(x["patch"].encode("utf-8")) for x in files)
        self.assertGreater(size, 81920)
        plan = v2.build_plan(GH(files), pinned(15))
        self.assertEqual(len(plan.files), 15)
        self.assertIn("tests/unit/test_encerramento_gui.py", plan.files)
        self.assertEqual(plan.added_total, 15 * 35)
        self.assertEqual(len(plan.anchors), 15 * 35)
        self.assertGreater(len(plan.chunks), 1)
        self.assertLessEqual(len(plan.chunks), v2.MAX_CHUNKS)
        self.assertEqual(set(plan.files),
                         set(p.path for c in plan.chunks for p in c.parts))

    def test_pr65_scale_57_hunks_full_refutation_preflight(self):
        # Offline geometric fixture based on measured PR #65: 15 files,
        # 57 hunks, 1241 added lines, 259 deleted lines, 511 context lines.
        files = []
        hunk_num = 0
        for index in range(14):
            old_pos = 1
            new_pos = 1
            hunks = []
            file_additions = file_deletions = 0
            for _ in range(4):
                changed = 16 + (hunk_num < 25)
                removed = 4 + (hunk_num < 35)
                context = 9 + (hunk_num < 7)
                old_count = removed + context
                new_count = changed + context
                rows = (
                    [" keep_context_{}_{}_{}".format(index, hunk_num, i)
                     for i in range(context)] +
                    ["-previous_value_{}_{}".format(hunk_num, i)
                     for i in range(removed)] +
                    ["+value_{}_{} = {}".format(hunk_num, i, "x" * 35)
                     for i in range(changed)]
                )
                hunks.append(
                    "@@ -{},{} +{},{} @@\n{}".format(
                        old_pos, old_count, new_pos, new_count,
                        "\n".join(rows)))
                old_pos += old_count + 4
                new_pos += new_count + 4
                file_additions += changed
                file_deletions += removed
                hunk_num += 1
            files.append({
                "filename": "src/feature_{:02}.py".format(index),
                "status": "modified", "patch": "\n".join(hunks),
                "additions": file_additions, "deletions": file_deletions,
            })
        big = {
            "filename": "tests/unit/test_encerramento_gui.py",
            "status": "added",
            "patch": "@@ -0,0 +1,320 @@\n" +
                     "\n".join("+test_worker_path_{} = {}".format(i, "x"*55)
                               for i in range(320)),
            "additions": 320, "deletions": 0,
        }
        files.append(big)
        self.assertEqual(hunk_num + 1, 57)
        self.assertEqual(sum(f["additions"] for f in files), 1241)
        self.assertEqual(sum(f["deletions"] for f in files), 259)
        self.assertGreater(len(big["patch"].encode("utf-8")), 20_000)
        diff_bytes = sum(len(f["patch"].encode("utf-8")) for f in files)
        self.assertGreater(diff_bytes, 81920)
        self.assertLess(diff_bytes, 125000)
        snap = runner.Snapshot(77, BASE, HEAD, "Real-scale SQLite review",
                               "Technical description " * 100, len(files))
        plan = v2.build_plan(GH(files), snap)
        self.assertEqual(len(plan.files), 15)
        self.assertEqual(plan.added_total, 1241)
        self.assertEqual(plan.hunks_original, 57)
        self.assertEqual(plan.coverage["hunks_supplied"], 57)
        self.assertGreater(plan.coverage["fragments_supplied"], 57)
        self.assertEqual(plan.coverage["test_files_supplied"], 1)
        self.assertEqual(len(plan.anchors), 1241)
        self.assertLessEqual(len(plan.chunks), v2.MAX_CHUNKS)
        refutation = v2.Chunk(
            0,
            tuple(p for chunk in plan.chunks for p in chunk.parts),
            tuple(a for chunk in plan.chunks for a in chunk.anchors),
        )
        self.assertGreater(contract.refutation_preflight(snap, plan, refutation), 0)
        discovery = [contract.make_payload(snap, plan, c) for c in plan.chunks]
        self.assertLessEqual(contract.worst_case_usd(discovery),
                             contract.MAX_TOTAL_USD)

    def test_one_large_hunk_is_partitioned_with_original_new_coordinates(self):
        entry = fixture("src/huge.py", new_lines=180, width=100)
        segments = v2.split_patch(entry["filename"], entry["patch"], "modified")
        self.assertGreater(len(segments), 2)
        self.assertEqual(sum(p.additions for p in segments), 180)
        self.assertEqual(sum(p.deletions for p in segments), 180)
        plan = v2.build_plan(GH([entry]), pinned(1))
        coords = sorted(a[2] for a in plan.anchors.values())
        self.assertEqual(coords, list(range(1, 181)))
        for part in segments:
            self.assertLessEqual(len(part.patch.encode("utf-8")),
                                 v2.MAX_FRAGMENT_BYTES)
            runner.parse_patch(part.patch, part.additions, part.deletions)

    def test_removed_file_is_anchored_at_file_level(self):
        entry = {
            "filename": "src/removed.py", "status": "removed",
            "patch": "@@ -1,1 +0,0 @@\n-deleted_value = True",
            "additions": 0, "deletions": 1,
        }
        plan = v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(list(plan.anchors.values())[0][2], None)

    def test_manifest_hash_binds_unanchored_context_and_deletions(self):
        original = {
            "filename": "src/sample.py", "status": "modified",
            "patch": "@@ -1,2 +1,2 @@\n keep\n-old\n+new_value = 10",
            "additions": 1, "deletions": 1,
        }
        alternate = dict(original)
        alternate["patch"] = original["patch"].replace(" keep", " changed_keep")
        p1 = v2.build_plan(GH([original]), pinned(1))
        p2 = v2.build_plan(GH([alternate]), pinned(1))
        self.assertEqual(list(p1.anchors.values()), list(p2.anchors.values()))
        self.assertNotEqual(p1.digest, p2.digest)

    def test_only_added_lines_receive_line_anchors(self):
        entry = {
            "filename": "src/with_context.py", "status": "modified",
            "patch": "@@ -1,3 +1,3 @@\n keep_before\n-old\n+new_value = True\n keep_after",
            "additions": 1, "deletions": 1,
        }
        plan = v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(len(plan.anchors), 1)
        self.assertEqual(next(iter(plan.anchors.values()))[2], 2)
        self.assertIn(" keep_after", plan.chunks[0].parts[0].patch)

    def test_deletion_only_with_context_has_file_anchor(self):
        entry = {
            "filename": "src/deletion.py", "status": "modified",
            "patch": "@@ -1,2 +1,1 @@\n keep_context\n-delete_me",
            "additions": 0, "deletions": 1,
        }
        plan = v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(len(plan.anchors), 1)
        self.assertIsNone(next(iter(plan.anchors.values()))[2])

    def test_no_newline_markers_remain_attached_to_original_line(self):
        entry = {
            "filename": "src/eof.py", "status": "modified",
            "patch": "@@ -1,1 +1,1 @@\n-previous\n\\ No newline at end of file\n+next_value = True\n\\ No newline at end of file",
            "additions": 1, "deletions": 1,
        }
        plan = v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(len(plan.chunks), 1)
        self.assertIn(r"\ No newline at end of file",
                      plan.chunks[0].parts[0].patch)

    def test_oversize_and_budget_fail_closed_without_omission(self):
        entry = fixture("src/too_big.py", new_lines=1, width=9500)
        with self.assertRaises(Problem) as err:
            v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(err.exception.code, "v2_fragment_oversize")

        files = [fixture(f"src/{i}.py", new_lines=75, width=140)
                 for i in range(35)]
        with patch.object(v2, "MAX_CHUNKS", 2):
            with self.assertRaises(Problem) as err:
                v2.build_plan(GH(files), pinned(35))
        self.assertEqual(err.exception.code, "v2_chunk_limit")

    def test_missing_patch_and_path_traversal_fail_closed(self):
        for change in ({"patch": None}, {"filename": "../escape.py"},
                       {"additions": 99}):
            entry = fixture()
            entry.update(change)
            with self.subTest(change=change), self.assertRaises(Problem):
                v2.build_plan(GH([entry]), pinned(1))

    def test_updated_head_is_rejected_after_plan(self):
        gh = GH([fixture()])
        gh.head = "c" * 40
        with self.assertRaises(Problem) as err:
            v2.build_plan(gh, pinned(1))
        self.assertEqual(err.exception.code, "pr_updated")

    def test_pr_data_remains_inert_and_unicode_crlf_is_preserved(self):
        entry = fixture("src/olá.py", new_lines=3, width=18)
        entry["patch"] = entry["patch"].replace("\n", "\r\n")
        with patch("subprocess.run", side_effect=AssertionError("execute")):
            plan = v2.build_plan(GH([entry]), pinned(1))
        self.assertEqual(plan.added_total, 3)
        self.assertTrue(all(x[0].startswith("a") for x in plan.anchors.values()))


if __name__ == "__main__":
    unittest.main()
