import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from operator_use.web.tools.browser import browser


@pytest.mark.asyncio
async def test_browser_tool_tab_close_rejects_last_tab():
    browser_instance = MagicMock()
    browser_instance._client = object()
    browser_instance.current_page = MagicMock()
    browser_instance.get_all_tabs = AsyncMock(return_value=[MagicMock()])

    result = await browser.ainvoke(action="tab", tab_mode="close", browser=browser_instance)

    assert result.success is True
    assert "Cannot close the last remaining tab." in result.output


@pytest.mark.asyncio
async def test_browser_tool_upload_reports_missing_files():
    browser_instance = MagicMock()
    browser_instance._client = object()
    browser_instance.current_page = MagicMock(return_value=MagicMock())

    with patch.object(Path, "exists", return_value=False):
        result = await browser.ainvoke(
            action="upload",
            x=1,
            y=2,
            filenames=["missing.txt"],
            browser=browser_instance,
        )

    assert result.success is False
    assert "Upload files not found" in result.error
