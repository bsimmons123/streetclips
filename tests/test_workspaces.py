from streetclip import workspaces


def test_job_dir_is_zero_padded(tmp_path):
    assert workspaces.job_dir(tmp_path, 7).name == "job_00007"


def test_purge_removes_only_the_intermediate_audio(tmp_path):
    work = workspaces.job_dir(tmp_path, 1) / "work"
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"x" * 2048)
    (work / "keepme.ass").write_text("subtitle")

    freed = workspaces.purge_intermediates(tmp_path, 1)

    assert freed == 2048
    assert not (work / "audio.wav").exists()
    assert (work / "keepme.ass").exists(), "the renderer reuses this directory"
    assert work.is_dir()


def test_purge_is_safe_when_nothing_is_there(tmp_path):
    assert workspaces.purge_intermediates(tmp_path, 99) == 0


def test_delete_workspace_removes_the_whole_directory(tmp_path):
    shorts = workspaces.job_dir(tmp_path, 1) / "shorts"
    shorts.mkdir(parents=True)
    (shorts / "01.mp4").write_bytes(b"video")

    workspaces.delete_workspace(tmp_path, 1)

    assert not workspaces.job_dir(tmp_path, 1).exists()


def test_delete_workspace_is_safe_when_absent(tmp_path):
    workspaces.delete_workspace(tmp_path, 42)  # must not raise


def test_source_is_shared_detects_another_reference(tmp_path):
    source = tmp_path / "uploads" / "a.mp4"
    assert workspaces.source_is_shared(source, [str(source), str(source)])
    assert not workspaces.source_is_shared(source, [str(source), "/elsewhere/b.mp4"])
    assert not workspaces.source_is_shared(source, ["/elsewhere/b.mp4"])
