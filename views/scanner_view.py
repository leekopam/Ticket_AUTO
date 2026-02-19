"""
QR 코드 스캔
"""
import cv2
from pyzbar.pyzbar import decode


class ScannerView:
    """QR 코드 스캐너 UI"""
    
    def __init__(self):
        # 카메라 초기화 (한 번만 실행)
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            print("카메라를 열 수 없습니다.")
        
        # 스캔 활성화 상태 (처리중일 때 False로 설정하여 스캔 차단)
        self._is_scanning_enabled = True
    
    def set_scanning_enabled(self, enabled: bool) -> None:
        """스캔 활성화/비활성화 설정"""
        self._is_scanning_enabled = enabled
        print(f"스캔 {'활성화' if enabled else '비활성화'}")
    
    def scan_qr(self) -> str | None:
        """
        QR 코드를 스캔하고 URL 반환
        
        Returns:
            str: QR 코드 URL (성공 시)
            None: 사용자가 'q'키로 종료 시
        """
        if not self._cap or not self._cap.isOpened():
            return None
            
        result_url = None
        
        while True:
            ret, frame = self._cap.read()
            if not ret:
                break
            
            # 스캔 활성화 상태일 때만 QR 코드 인식
            if self._is_scanning_enabled:
                qr_codes = decode(frame)
                
                for qr in qr_codes:
                    result_url = qr.data.decode('utf-8')
                    break
                
                # QR 코드 발견 시 즉시 반환
                if result_url:
                    break
            
            cv2.imshow('QR Scanner', frame)
            
            # ESC 키로 종료 (ASCII 코드 27)
            if cv2.waitKey(1) & 0xFF == 27:
                break

        cv2.destroyAllWindows()
        return result_url
    
    def release(self) -> None:
        """리소스 해제 (프로그램 종료 시 호출)"""
        if self._cap:
            self._cap.release()
        cv2.destroyAllWindows()
