from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode

from flask import redirect, request

import room_mapping_store as store
import room_type_enrichment
import runner


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _back(**params: str):
    query = urlencode(params)
    return redirect("/room-mappings" + (f"?{query}" if query else ""))


def _form_data() -> dict[str, str]:
    return {name: str(request.form.get(name, "")).strip() for name in store.FIELDS}


def _select(name: str, label: str, options: list[str], current: Any) -> str:
    values = list(options)
    current = str(current or "")
    if current and current not in values:
        values.insert(0, current)
    choices = ["<option value=''>请选择</option>"]
    for value in values:
        selected = " selected" if value == current else ""
        choices.append(f"<option value='{esc(value)}'{selected}>{esc(value)}</option>")
    return (
        f"<div class='field'><label for='{esc(name)}'>{esc(label)}</label>"
        f"<select id='{esc(name)}' name='{esc(name)}' required>"
        f"{''.join(choices)}</select></div>"
    )


def _input(name: str, value: Any, placeholder: str, required: bool = True) -> str:
    required_attr = " required" if required else ""
    return (
        f"<div class='field'><label for='{esc(name)}'>{esc(store.LABELS[name])}</label>"
        f"<input id='{esc(name)}' name='{esc(name)}' value='{esc(value)}' "
        f"placeholder='{esc(placeholder)}'{required_attr}></div>"
    )


def _datalist_input(
    name: str, value: Any, options: list[str], required: bool = True
) -> str:
    required_attr = " required" if required else ""
    choices = "".join(f"<option value='{esc(item)}'></option>" for item in options)
    return (
        f"<div class='field'><label for='{esc(name)}'>{esc(store.LABELS[name])}</label>"
        f"<input id='{esc(name)}' name='{esc(name)}' list='{esc(name)}_list' "
        f"value='{esc(value)}' placeholder='请选择或直接输入（选填）'{required_attr}>"
        f"<datalist id='{esc(name)}_list'>{choices}</datalist></div>"
    )


def _render_form(
    item: dict[str, Any],
    pms_names: list[str],
    meituan_names: list[str],
    ctrip_names: list[str],
    editing: bool,
) -> str:
    cancel = " <a class='button secondary' href='/room-mappings'>取消编辑</a>" if editing else ""
    return f"""
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2 style="margin:0 0 6px">{'编辑' if editing else '新增'}统一房型</h2>
          <div class="muted">三个平台可使用不同酒店名，所选房型共享统一房型 ID。</div>
        </div>
        <span class="pill idle">PMS ↔ 美团 ↔ 携程</span>
      </div>
      <form method="post" action="/room-mappings/save" class="form-grid" style="margin-top:16px">
        <input type="hidden" name="original_room_type_id"
               value="{esc(item.get('room_type_id') if editing else '')}">
        <input type="hidden" name="original_hotel_id"
               value="{esc(item.get('hotel_id') if editing else '')}">
        {_input("hotel_id", item.get("hotel_id", ""), "例如 puyue")}
        {_input("pms_hotel_name", item.get("pms_hotel_name", ""), "自动同步，可手动修改")}
        {_input("hotel_name", item.get("hotel_name", ""), "美团酒店展示名称")}
        {_input("ctrip_hotel_name", item.get("ctrip_hotel_name", ""), "携程酒店展示名称（选填）", False)}
        {_input("room_type_id", item.get("room_type_id", ""), "自定义，例如 PY01")}
        {_input(
            "room_type_name",
            item.get("room_type_name", ""),
            "选填；留空时使用 PMS 房型名称",
            False,
        )}
        {_select("pms_room_type_name", store.LABELS["pms_room_type_name"], pms_names, item.get("pms_room_type_name"))}
        {_select("meituan_room_type_name", store.LABELS["meituan_room_type_name"], meituan_names, item.get("meituan_room_type_name"))}
        {_datalist_input("ctrip_room_type_name", item.get("ctrip_room_type_name", ""), ctrip_names, False)}
        <div class="field" style="grid-column:1/-1">
          <button type="submit">{'保存修改' if editing else '保存映射'}</button>{cancel}
        </div>
      </form>
    </section>
    """


def _render_rows(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return "<tr><td colspan='8' class='muted'>暂无统一房型映射。</td></tr>"
    rows = []
    for item in groups:
        active = int(item["is_active"]) == 1
        query = urlencode({"hotel_id": item["hotel_id"], "edit": item["room_type_id"]})
        rows.append(
            f"""
            <tr>
              <td>{esc(item["room_type_id"])}<br><span class="muted">{esc(item["room_type_name"])}</span></td>
              <td>{esc(item["pms_room_type_name"]) or '<span class="muted">未选择</span>'}</td>
              <td>{esc(item["meituan_room_type_name"]) or '<span class="muted">未选择</span>'}</td>
              <td>{esc(item["ctrip_room_type_name"]) or '<span class="muted">未选择</span>'}</td>
              <td><span class="pill {'good' if active else 'idle'}">{'启用' if active else '停用'}</span></td>
              <td class="muted">{esc(item["updated_at"])}</td>
              <td><div class="actions">
                <a class="button secondary compact" href="/room-mappings?{query}">编辑</a>
                <form method="post" action="/room-mappings/toggle">
                  <input type="hidden" name="hotel_id" value="{esc(item["hotel_id"])}">
                  <input type="hidden" name="room_type_id" value="{esc(item["room_type_id"])}">
                  <input type="hidden" name="active" value="{'0' if active else '1'}">
                  <button class="secondary compact" type="submit">{'停用' if active else '启用'}</button>
                </form>
              </div></td>
            </tr>
            """
        )
    return "".join(rows)


def _render_page(
    page_func, groups, item, pms_names, meituan_names, ctrip_names, editing
) -> str:
    messages = ""
    if request.args.get("notice"):
        messages += f"<div class='success'>{esc(request.args['notice'])}</div>"
    if request.args.get("error"):
        messages += f"<div class='warning'>{esc(request.args['error'])}</div>"
    body = f"""
    {messages}
    {_render_form(item, pms_names, meituan_names, ctrip_names, editing)}
    <section class="panel">
      <div class="panel-heading">
        <div><h2 style="margin:0 0 6px">统一房型映射</h2>
        <div class="muted">每一行代表一个内部房型及其 PMS、美团、携程对应名称。</div></div>
        <span class="pill idle">{len(groups)} 个房型</span>
      </div>
      <div class="table-wrap" style="margin-top:14px"><table>
        <tr><th>统一房型</th><th>PMS 房型</th><th>美团房型</th><th>携程房型</th>
        <th>状态</th><th>更新时间</th><th>操作</th></tr>
        {_render_rows(groups)}
      </table></div>
    </section>
    """
    return page_func("房型映射", body, "room_mappings")


def register(app, page_func) -> None:
    @app.get("/room-mappings")
    def room_mappings_page() -> str:
        settings = runner.load_settings()
        try:
            groups = store.list_groups(settings)
            pms_hotels, pms_names, meituan_names, ctrip_names = store.room_options(settings)
            hotel_id = str(request.args.get("hotel_id", "")).strip()
            room_id = str(request.args.get("edit", "")).strip()
            item = store.get_group(settings, hotel_id, room_id) if hotel_id and room_id else None
            editing = item is not None
            default = store.defaults(settings)
            if not default["pms_hotel_name"] and pms_hotels:
                default["pms_hotel_name"] = pms_hotels[0]
            if item:
                item["pms_hotel_name"] = item["pms_hotel_name"] or default["pms_hotel_name"]
                item["hotel_name"] = item["hotel_name"] or default["hotel_name"]
                item["ctrip_hotel_name"] = (
                    item["ctrip_hotel_name"] or default["ctrip_hotel_name"]
                )
            return _render_page(
                page_func,
                groups,
                item or default,
                pms_names,
                meituan_names,
                ctrip_names,
                editing,
            )
        except Exception as exc:
            body = f"<section class='panel'><div class='warning'>{esc(store.error_message(exc))}</div></section>"
            return page_func("房型映射", body, "room_mappings")

    @app.post("/room-mappings/save")
    def room_mappings_save():
        settings = runner.load_settings()
        data = _form_data()
        if not data["room_type_name"]:
            data["room_type_name"] = data["pms_room_type_name"]
        error = store.validate(data)
        if error:
            return _back(error=error)
        try:
            change = store.save_group(
                settings,
                data,
                str(request.form.get("original_hotel_id", "")).strip(),
                str(request.form.get("original_room_type_id", "")).strip(),
            )
        except Exception as exc:
            return _back(error=store.error_message(exc))
        try:
            stats = room_type_enrichment.enrich_mapping_change(settings, change)
            matched = sum(
                item["matched_by_product"] + item["matched_by_name"]
                for item in stats.values()
            )
            return _back(notice=f"统一房型映射已保存，已关联 {matched} 条历史数据")
        except Exception as exc:
            return _back(notice=f"映射已保存；历史数据将在下次采集时补齐：{exc}")

    @app.post("/room-mappings/toggle")
    def room_mappings_toggle():
        settings = runner.load_settings()
        try:
            change = store.set_active(
                settings,
                str(request.form.get("hotel_id", "")).strip(),
                str(request.form.get("room_type_id", "")).strip(),
                request.form.get("active") == "1",
            )
            if change:
                room_type_enrichment.enrich_mapping_change(settings, change)
            return _back(notice="状态已更新" if change else "未找到映射")
        except Exception as exc:
            return _back(error=store.error_message(exc))
