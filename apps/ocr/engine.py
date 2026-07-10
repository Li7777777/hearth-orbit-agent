import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_ocr_instance = None
_ocr_lock = threading.Lock()


def get_ocr_engine():
    """获取 PaddleOCR 单例（线程安全，懒加载）"""
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                try:
                    from paddleocr import PaddleOCR
                    _ocr_instance = PaddleOCR(
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        ocr_version='PP-OCRv4',
                        enable_mkldnn=False,
                    )
                    logger.info("PaddleOCR 引擎初始化成功 (PP-OCRv4, MKLDNN=off)")
                except Exception as e:
                    logger.error(f"PaddleOCR 初始化失败: {e}")
                    raise
    return _ocr_instance


def recognize_image(image_path: str | Path) -> list:
    """
    识别图片文本，返回按垂直位置排序的文本行列表。
    每个元素: {'text': str, 'confidence': float, 'y_pos': float}
    """
    engine = get_ocr_engine()
    result = engine.predict(input=str(image_path))

    lines = []
    if not result:
        return lines

    for page in result:
        rec_texts = page.get('rec_texts', [])
        rec_scores = page.get('rec_scores', [])
        dt_polys = page.get('dt_polys', [])

        for text, score, poly in zip(rec_texts, rec_scores, dt_polys):
            if not text or not text.strip():
                continue
            try:
                xs = [float(point[0]) for point in poly]
                ys = [float(point[1]) for point in poly]
                x_min = min(xs)
                x_max = max(xs)
                y_min = min(ys)
                y_max = max(ys)
                x_center = sum(xs) / len(xs)
                y_center = sum(ys) / len(ys)
            except (IndexError, TypeError):
                x_min = x_max = x_center = 0
                y_min = y_max = y_center = 0

            text = text.strip()
            if not text:
                continue

            lines.append({
                'text': text,
                'confidence': float(score),
                'x_min': float(x_min),
                'x_max': float(x_max),
                'x_center': float(x_center),
                'y_min': float(y_min),
                'y_max': float(y_max),
                'y_pos': float(y_center),
            })

    lines.sort(key=lambda x: x['y_pos'])
    return lines
