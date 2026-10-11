"""
报表生成 API
"""
from fastapi import APIRouter, HTTPException
from services.report_service import ReportFormat, ReportType, get_report_service

router = APIRouter(prefix="/reports", tags=["报表管理"])


# ========== 报表模板 ==========


@router.get("/templates")
def list_templates():
    """列出报表模板"""
    service = get_report_service()
    return {"items": service.list_templates()}


# ========== 报表生成 ==========


@router.post("")
def create_report(req: dict):
    """创建报表"""
    service = get_report_service()
    report = service.create_report(
        name=req.get("name"),
        report_type=req.get("report_type", ReportType.EQUIPMENT_STATUS),
        report_format=req.get("report_format", ReportFormat.PDF),
        date_from=req.get("date_from"),
        date_to=req.get("date_to"),
        created_by=req.get("created_by", "system"),
    )
    return {
        "id": report.id,
        "name": report.name,
        "status": report.status,
        "created_at": report.created_at,
    }


@router.post("/{report_id}/generate")
def generate_report(report_id: str):
    """生成报表"""
    service = get_report_service()
    report = service.generate_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报表不存在")
    return {"id": report.id, "status": report.status, "file_url": report.file_url}


# ========== 报表查询 ==========


@router.get("/{report_id}")
def get_report(report_id: str):
    """获取报表详情"""
    service = get_report_service()
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报表不存在")

    result = {
        "id": report.id,
        "name": report.name,
        "report_type": report.report_type,
        "report_format": report.report_format,
        "date_from": report.date_from,
        "date_to": report.date_to,
        "created_by": report.created_by,
        "created_at": report.created_at,
        "status": report.status,
        "file_url": report.file_url,
        "size": report.size,
    }

    if report.status == "completed" and hasattr(report, "data"):
        result["data"] = report.data

    return result


@router.get("")
def list_reports(report_type: str = None, status: str = None, limit: int = 50):
    """列出报表"""
    service = get_report_service()
    return {
        "total": len(service.reports),
        "items": service.list_reports(report_type, status, limit),
    }


# ========== 报表下载 ==========


@router.get("/{report_id}/download")
def download_report(report_id: str):
    """下载报表 — 生成真实 CSV 文件下载"""
    import io as _io
    from fastapi.responses import StreamingResponse
    service = get_report_service()
    report = service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报表不存在")

    # get_report 返回 Report 对象,转 dict
    if hasattr(report, 'to_dict'):
        report = report.to_dict()
    elif not isinstance(report, dict):
        report = {"name": str(report), "report_type": "unknown"}

    # 生成 CSV 内容
    csv_lines = []
    csv_lines.append(f"# 报表: {report.get('name', report_id)}")
    csv_lines.append(f"# 类型: {report.get('report_type', '')}")
    csv_lines.append(f"# 格式: CSV")
    csv_lines.append(f"# 生成时间: {report.get('created_at', '')}")
    csv_lines.append("")
    data = report.get("data") or report.get("content") or {}
    if isinstance(data, dict) and data:
        csv_lines.append(",".join(data.keys()))
        csv_lines.append(",".join(str(v) for v in data.values()))
    elif isinstance(data, list) and data:
        if isinstance(data[0], dict):
            cols = list(data[0].keys())
            csv_lines.append(",".join(cols))
            for row in data:
                csv_lines.append(",".join(str(row.get(c, "")) for c in cols))
        else:
            csv_lines.extend(str(r) for r in data)
    else:
        csv_lines.append("指标,值")
        for k, v in report.items():
            if k not in ("id", "name") and v is not None:
                csv_lines.append(f"{k},{v}")
    csv_content = "\n".join(csv_lines).encode("utf-8-sig")
    filename = f"{report.get('name', report_id)}.csv".replace("/", "_")

    return StreamingResponse(
        _io.BytesIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ========== 快速报表 ==========


@router.get("/quick/equipment-status")
def quick_equipment_report(date_from: str, date_to: str):
    """快速设备状态报表"""
    service = get_report_service()
    report = service._generate_equipment_report(date_from, date_to)
    return report


@router.get("/quick/energy-consumption")
def quick_energy_report(date_from: str, date_to: str):
    """快速能耗报表"""
    service = get_report_service()
    report = service._generate_energy_report(date_from, date_to)
    return report


@router.get("/quick/alert-statistics")
def quick_alert_report(date_from: str, date_to: str):
    """快速告警统计报表"""
    service = get_report_service()
    report = service._generate_alert_report(date_from, date_to)
    return report
