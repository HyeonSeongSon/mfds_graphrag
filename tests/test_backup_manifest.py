"""scripts/backup_manifest.py: 목록 생성(폴더 · 파일 대상), sha256sum 형식, 대조(같음 · 다름 · 없음)."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_manifest as M  # noqa: E402


def _tree(root: Path) -> None:
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "x.txt").write_bytes(b"x")
    (root / "a" / "y.txt").write_bytes(b"yy")
    (root / "z.json").write_bytes(b"{}")


def test_make_lists_folder_and_file_targets(tmp_path: Path) -> None:
    _tree(tmp_path)
    text, size = M.make(tmp_path, ["a", "z.json"])
    got = M.parse(text)
    assert sorted(got) == ["a/b/x.txt", "a/y.txt", "z.json"] and size == 5
    assert got["a/y.txt"] == M.sha256(tmp_path / "a" / "y.txt")
    assert text.splitlines()[0][64:66] == "  "  # sha256sum 형식


def test_missing_target_fails(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        M.files_of(tmp_path, ["nope"])


def test_verify_counts_same_changed_missing(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    _tree(src)
    exp = M.parse(M.make(src, ["a", "z.json"])[0])
    shutil.copytree(src, dst)
    assert M.verify(exp, dst) == {"같음": 3, "다름": 0, "없음": 0}
    (dst / "a" / "y.txt").write_bytes(b"changed")
    (dst / "z.json").unlink()
    assert M.verify(exp, dst) == {"같음": 1, "다름": 1, "없음": 1}
