"""
文档服务模块
"""
from pathlib import Path
from typing import Optional


class DocumentService:
    """文档解析服务"""

    async def parse(self, file_path: str, file_type: str) -> str:
        """解析文档"""
        if file_type == ".pdf":
            return await self._parse_pdf(file_path)
        elif file_type == ".docx":
            return await self._parse_docx(file_path)
        elif file_type == ".md":
            return await self._parse_markdown(file_path)
        elif file_type == ".txt":
            return await self._parse_text(file_path)
        else:
            raise ValueError(f"不支持的文件类型: {file_type}")

    async def _parse_pdf(self, file_path: str) -> str:
        """解析PDF文件"""
        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(file_path)
            text_parts = []

            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

            return "\n\n".join(text_parts)
        except Exception as e:
            raise Exception(f"PDF解析失败: {str(e)}")

    async def _parse_docx(self, file_path: str) -> str:
        """解析Word文件"""
        try:
            from docx import Document

            doc = Document(file_path)
            text_parts = []

            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)

            return "\n\n".join(text_parts)
        except Exception as e:
            raise Exception(f"Word解析失败: {str(e)}")

    async def _parse_markdown(self, file_path: str) -> str:
        """解析Markdown文件"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            raise Exception(f"Markdown解析失败: {str(e)}")

    async def _parse_text(self, file_path: str) -> str:
        """解析纯文本文件"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            raise Exception(f"文本解析失败: {str(e)}")
