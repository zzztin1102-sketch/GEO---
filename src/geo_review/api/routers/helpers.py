"""Route helper functions shared across routers."""

import os
from typing import Any

from fastapi.responses import HTMLResponse, JSONResponse

from geo_review.result.builder import ReviewResultFormatter
from geo_review.result.models import ReviewResponse


def get_file_extension(filename: str) -> str:
    """从文件名提取扩展名."""
    if not filename:
        raise ValueError("文件名不能为空")
    ext = os.path.splitext(filename)[1]
    if not ext:
        raise ValueError(f"文件名 '{filename}' 无扩展名")
    return ext[1:].lower()


def format_response(response: ReviewResponse, output_format: str) -> Any:
    """根据格式要求返回响应."""
    if output_format == "markdown":
        md = ReviewResultFormatter.to_markdown(response)
        return JSONResponse(
            content={
                "format": "markdown",
                "content": md,
                "review_id": str(response.review_id),
                "verdict": response.verdict.value,
            }
        )
    elif output_format == "html":
        html = ReviewResultFormatter.to_html(response)
        return HTMLResponse(content=html)
    else:
        # JSON 格式
        data = response.model_dump(mode="json")
        return JSONResponse(content=data)
