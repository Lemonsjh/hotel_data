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


def _posted_names(field: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in request.form.getlist(field):
        name = str(value or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _form_data() -> dict[str, Any]:
    data: dict[str, Any] = {
        name: str(request.form.get(name, "")).strip() for name in store.FIELDS
    }
    if request.form.get("dual_pms_aliases") == "1":
        meituan_names = _posted_names("meituan_pms_room_type_names")
        rezen_names = _posted_names("rezen_pms_room_type_names")
        union = meituan_names + [name for name in rezen_names if name not in set(meituan_names)]
        data["dual_pms_aliases"] = True
        data["meituan_pms_room_type_names"] = meituan_names
        data["rezen_pms_room_type_names"] = rezen_names
        data[store.PMS_ALIASES_FIELD] = union
        data["pms_room_type_name"] = union[0] if union else ""
        return data
    pms_names = _posted_names(store.PMS_ALIASES_FIELD)
    selected_name = str(request.form.get("pms_room_type_name", "")).strip()
    if selected_name and selected_name not in pms_names:
        pms_names.append(selected_name)
    data[store.PMS_ALIASES_FIELD] = pms_names
    data["pms_room_type_name"] = pms_names[0] if pms_names else ""
    return data


def _select(name: str, label: str, options: list[str], current: Any, required: bool = True) -> str:
    required_attr = " required" if required else ""
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
        f"<select id='{esc(name)}' name='{esc(name)}'{required_attr}>"
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
        f"value='{esc(value)}' autocomplete='off' placeholder='请选择或直接输入（选填）'{required_attr}>"
        f"<datalist id='{esc(name)}_list'>{choices}</datalist></div>"
    )


def _dual_side(name: str, label: str, options: list[str], selected: Any) -> str:
    """双 PMS 分侧房型：使用与其它字段一致的下拉框，已关联的其它别名用隐藏域保留。"""
    chosen = [str(value).strip() for value in (selected or []) if str(value).strip()]
    preserved = "".join(
        f"<input type='hidden' name='{esc(name)}' value='{esc(value)}'>" for value in chosen
    )
    return preserved + _select(name, label, options, chosen[0] if chosen else "", False)


def _render_form(
    item: dict[str, Any],
    pms_names: list[str],
    meituan_names: list[str],
    ctrip_names: list[str],
    editing: bool,
    sides=None,
) -> str:
    cancel = " <a class='button secondary' href='/room-mappings'>取消编辑</a>" if editing else ""
    aliases = item.get(store.PMS_ALIASES_FIELD) or []
    current_pms = item.get("pms_room_type_name") or (aliases[0] if aliases else "")
    if sides is None:
        preserved_aliases = (
            "".join(
                f"<input type='hidden' name='{esc(store.PMS_ALIASES_FIELD)}' value='{esc(value)}'>"
                for value in aliases
                if str(value or "").strip()
            )
            if editing
            else ""
        )
        pms_fields = preserved_aliases + _select(
            "pms_room_type_name", store.LABELS["pms_room_type_name"], pms_names, current_pms
        )
        hint = "美团、携程房型至少填写一个，可分次补充。所选房型共享统一房型 ID。"
    else:
        meituan_options, rezen_options = sides
        pms_fields = (
            "<input type='hidden' name='dual_pms_aliases' value='1'>"
            + _dual_side("meituan_pms_room_type_names", "美团 PMS 房型", meituan_options,
                         item.get("meituan_pms_room_type_names"))
            + _dual_side("rezen_pms_room_type_names", "Rezen 房型", rezen_options,
                         item.get("rezen_pms_room_type_names"))
        )
        hint = "美团 PMS 与 Rezen 的原始房型分开选择，可以对应同一个统一房型；已关联的其它别名会保留。"
    return f"""
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2 style="margin:0 0 6px">{'编辑' if editing else '新增'}统一房型</h2>
          <div class="muted">{hint}</div>
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
        {_input("hotel_name", item.get("hotel_name", ""), "填写美团房型时必填", False)}
        {_input("ctrip_hotel_name", item.get("ctrip_hotel_name", ""), "填写携程房型时必填", False)}
        {_input("room_type_id", item.get("room_type_id", ""), "自定义，例如 PY01")}
        {_input(
            "room_type_name",
            item.get("room_type_name", ""),
            "选填；留空时使用 PMS 房型名称",
            False,
        )}
        {pms_fields}
        {_select("meituan_room_type_name", store.LABELS["meituan_room_type_name"], meituan_names, item.get("meituan_room_type_name"), False)}
        {_datalist_input("ctrip_room_type_name", item.get("ctrip_room_type_name", ""), ctrip_names, False)}
        <div class="field" style="grid-column:1/-1">
          <button type="submit">{'保存修改' if editing else '保存映射'}</button>{cancel}
        </div>
      </form>
    </section>
    """


def _pms_alias_html(item: dict[str, Any]) -> str:
    name = str(item.get("pms_room_type_name") or "").strip()
    if not name:
        return '<span class="muted">未选择</span>'
    label = str(item.get("pms_side_label") or "").strip()
    return esc(f"{label}：{name}" if label else name)


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
              <td>{_pms_alias_html(item)}</td>
              <td>{esc(item["meituan_room_type_name"]) or '<span class="muted">未选择</span>'}</td>
              <td>{esc(item["ctrip_room_type_name"]) or '<span class="muted">未选择</span>'}</td>
              <td><span class="pill {'good' if active else 'idle'}">{'启用' if active else '停用'}</span></td>
              <td class="muted">{esc(item["updated_at"])}</td>
              <td><div class="actions">
                <a class="button secondary compact" href="/room-mappings?{query}">编辑</a>
                <form method="post" action="/room-mappings/toggle">
                  <input type="hidden" name="hotel_id" value="{esc(item["hotel_id"])}">
                  <input type="hidden" name="room_type_id" value="{esc(item["room_type_id"])}">
                  <input type="hidden" name="pms_room_type_name" value="{esc(item["pms_room_type_name"])}">
                  <input type="hidden" name="source_platform" value="{esc(item.get("source_platform") or "")}">
                  <input type="hidden" name="active" value="{'0' if active else '1'}">
                  <button class="secondary compact" type="submit">{'停用' if active else '启用'}</button>
                </form>
              </div></td>
            </tr>
            """
        )
    return "".join(rows)


def _render_page(
    page_func, mapping_rows, item, pms_names, meituan_names, ctrip_names, editing, sides=None
) -> str:
    messages = ""
    if request.args.get("notice"):
        messages += f"<div class='success'>{esc(request.args['notice'])}</div>"
    if request.args.get("error"):
        messages += f"<div class='warning'>{esc(request.args['error'])}</div>"
    body = f"""
    {messages}
    {_render_form(item, pms_names, meituan_names, ctrip_names, editing, sides)}
    <section class="panel">
      <div class="panel-heading">
        <div><h2 style="margin:0 0 6px">统一房型映射</h2>
        <div class="muted">每一行代表一条 PMS 房型映射；同一统一房型可有多行。</div></div>
        <span class="pill idle">{len(mapping_rows)} 条 PMS 映射</span>
      </div>
      <div class="table-wrap" style="margin-top:14px"><table>
        <tr><th>统一房型</th><th>PMS 房型</th><th>美团房型</th><th>携程房型</th>
        <th>状态</th><th>更新时间</th><th>操作</th></tr>
        {_render_rows(mapping_rows)}
      </table></div>
    </section>
    """
    return page_func("房型映射", body, "room_mappings")


def register(app, page_func) -> None:
    @app.get("/room-mappings")
    def room_mappings_page() -> str:
        settings = runner.load_settings()
        try:
            mapping_rows = store.list_pms_mapping_rows(settings)
            pms_hotels, pms_names, meituan_names, ctrip_names = store.room_options(settings)
            sides = store.side_room_options(settings)
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
                if not item.get(store.PMS_ALIASES_FIELD) and item.get("pms_room_type_name"):
                    item[store.PMS_ALIASES_FIELD] = [item["pms_room_type_name"]]
            return _render_page(
                page_func,
                mapping_rows,
                item or default,
                pms_names,
                meituan_names,
                ctrip_names,
                editing,
                sides,
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
            change = store.set_pms_alias_active(
                settings,
                str(request.form.get("hotel_id", "")).strip(),
                str(request.form.get("room_type_id", "")).strip(),
                str(request.form.get("pms_room_type_name", "")).strip(),
                request.form.get("active") == "1",
                str(request.form.get("source_platform", "")).strip(),
            )
            if change:
                room_type_enrichment.enrich_mapping_change(settings, change)
            return _back(notice="状态已更新" if change else "未找到映射")
        except Exception as exc:
            return _back(error=store.error_message(exc))
