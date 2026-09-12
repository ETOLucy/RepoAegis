import repoaegis


def test_package_imports() -> None:
    assert repoaegis.__version__


async def test_async_mode_is_wired() -> None:
    # pytest-asyncio in auto mode: a bare ``async def`` test must run.
    assert True
