"""OCR API — 文字识别/文档理解/表格识别
使用EasyOCR + Transformers, CPU可运行"""
import json, os, uuid, io, base64
import numpy as np
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

router = APIRouter(prefix="/ocr", tags=["OCR识别"])

_reader_cache = {}

class OCRRequest(BaseModel):
    image_base64: str
    languages: List[str] = ["ch_sim", "en"]

@router.get("/status")
def ocr_status():
    has_easyocr = has_opencv = has_paddle = False
    try: import easyocr; has_easyocr = True
    except: pass
    try: import cv2; has_opencv = True
    except: pass
    try: from paddleocr import PaddleOCR; has_paddle = True
    except: pass
    return {
        "easyocr_available": has_easyocr,
        "opencv_available": has_opencv,
        "paddleocr_available": has_paddle,
        "supported_languages": ["ch_sim","ch_tra","en","ja","ko","fr","de","es","ar","ru"],
        "capabilities": ["text_detection","text_recognition","table_extraction","document_layout"]
    }

@router.post("/recognize")
def recognize_text(req: OCRRequest):
    """通用OCR识别"""
    try:
        import easyocr

        lang_key = "_".join(sorted(req.languages[:3]))
        if lang_key not in _reader_cache:
            _reader_cache[lang_key] = easyocr.Reader(req.languages[:3], gpu=False)
        reader = _reader_cache[lang_key]

        img_data = base64.b64decode(req.image_base64)
        img_array = np.frombuffer(img_data, np.uint8)
        import cv2
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        results = reader.readtext(img)

        return {
            "results": [{
                "text": r[1],
                "confidence": round(r[2], 4),
                "bbox": [[int(x) for x in pt] for pt in r[0]]
            } for r in results],
            "total_texts": len(results),
            "languages": req.languages
        }
    except Exception as e:
        return {"error": str(e), "results": []}

@router.post("/extract_text")
def extract_text(image_base64: str = Form("")):
    """提取纯文本"""
    try:
        import easyocr
        import cv2

        lang_key = "ch_sim_en"
        if lang_key not in _reader_cache:
            _reader_cache[lang_key] = easyocr.Reader(["ch_sim", "en"], gpu=False)
        reader = _reader_cache[lang_key]

        img_data = base64.b64decode(image_base64)
        img_array = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        results = reader.readtext(img)
        full_text = " ".join([r[1] for r in results])

        return {"text": full_text, "total_chars": len(full_text), "total_text_blocks": len(results)}
    except Exception as e:
        return {"error": str(e), "text": ""}

@router.post("/table")
def extract_table(image_base64: str = Form("")):
    """表格识别和提取"""
    try:
        import easyocr
        import cv2

        lang_key = "ch_sim_en"
        if lang_key not in _reader_cache:
            _reader_cache[lang_key] = easyocr.Reader(["ch_sim", "en"], gpu=False)
        reader = _reader_cache[lang_key]

        img_data = base64.b64decode(image_base64)
        img_array = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)

        results = reader.readtext(img)

        rows = {}
        for r in results:
            bbox, text, conf = r
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            row_key = int(y_center / 30)
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append({"text": text, "confidence": round(conf, 4), "x": bbox[0][0]})

        sorted_rows = []
        for k in sorted(rows.keys()):
            row = sorted(rows[k], key=lambda c: c["x"])
            sorted_rows.append({"cells": row, "row_text": " | ".join([c["text"] for c in row])})

        return {"table_rows": sorted_rows, "total_rows": len(sorted_rows), "total_cells": sum(len(r["cells"]) for r in sorted_rows)}
    except Exception as e:
        return {"error": str(e), "table_rows": []}

@router.post("/document")
def analyze_document(image_base64: str = Form("")):
    """文档整体分析 — 布局+文字+表格"""
    try:
        import easyocr
        import cv2

        lang_key = "ch_sim_en"
        if lang_key not in _reader_cache:
            _reader_cache[lang_key] = easyocr.Reader(["ch_sim", "en"], gpu=False)
        reader = _reader_cache[lang_key]

        img_data = base64.b64decode(image_base64)
        img_array = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        results = reader.readtext(img)
        full_text = " ".join([r[1] for r in results])

        height, width = img.shape[:2]
        regions = {"top": [], "middle": [], "bottom": []}
        for r in results:
            bbox, text, conf = r
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            if y_center < height * 0.33:
                regions["top"].append(text)
            elif y_center < height * 0.66:
                regions["middle"].append(text)
            else:
                regions["bottom"].append(text)

        return {
            "full_text": full_text,
            "text_blocks": len(results),
            "image_size": [width, height],
            "regions": {k: " ".join(v) for k, v in regions.items()},
            "average_confidence": round(sum(r[2] for r in results) / max(len(results), 1), 4)
        }
    except Exception as e:
        return {"error": str(e), "full_text": ""}

@router.get("/languages")
def list_languages():
    """列出支持的语言"""
    return {
        "languages": [
            {"code": "ch_sim", "name": "简体中文"},
            {"code": "ch_tra", "name": "繁体中文"},
            {"code": "en", "name": "英语"},
            {"code": "ja", "name": "日语"},
            {"code": "ko", "name": "韩语"},
            {"code": "fr", "name": "法语"},
            {"code": "de", "name": "德语"},
            {"code": "es", "name": "西班牙语"},
            {"code": "ar", "name": "阿拉伯语"},
            {"code": "ru", "name": "俄语"},
        ]
    }
