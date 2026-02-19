"""
에러 메시지 표시 창
"""
import tkinter as tk
from tkinter import font


class ErrorView:
    """에러 메시지 표시 UI"""
    
    def __init__(self):
        self._root = None
    
    def show(self, message: str) -> None:
        self._root = tk.Tk()
        self._root.title("오류")
        self._root.geometry("600x400")
        
        large_font = font.Font(size=20, weight="bold")
        
        tk.Label(self._root, text=message, font=large_font, fg="red").pack(pady=50)
        
        self._root.protocol("WM_DELETE_WINDOW", self._close)
        self._root.mainloop()
    
    def _close(self) -> None:
        """창 닫기"""
        if self._root:
            self._root.destroy()
            self._root = None
