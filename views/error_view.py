"""간단 오류 알림 뷰."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


class ErrorView:
    """치명적 오류를 단일 다이얼로그로 표시한다."""

    def show(self, message: str) -> None:
        # 임시 루트를 생성해 모달 다이얼로그를 띄우고 즉시 정리한다.
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror("오류", message, parent=root)
        finally:
            root.destroy()
