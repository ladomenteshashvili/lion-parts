from __future__ import annotations

import html
from typing import Any

import requests
import urllib3
import xmltodict
from django.conf import settings


class AMTProviderError(Exception):
    pass


def _oem_array_xml(oems: list[str], mode: str) -> str:
    if mode == "enc":
        inner = "".join(
            f'<item xsi:type="xsd:string">{html.escape(oem)}</item>'
            for oem in oems
        )
        return (
            f'<OemCodes soapenc:arrayType="xsd:string[{len(oems)}]" '
            f'xsi:type="soapenc:Array">{inner}</OemCodes>'
        )

    inner = "".join(f"<string>{html.escape(oem)}</string>" for oem in oems)
    return f"<OemCodes>{inner}</OemCodes>"


def _userparam_xml(login: str, password: str, mode: str) -> str:
    if mode == "struct":
        return (
            "<UserParam>"
            f"<login>{html.escape(login)}</login>"
            f"<passwd>{html.escape(password)}</passwd>"
            "</UserParam>"
        )

    if mode == "enc":
        return (
            '<UserParam soapenc:arrayType="xsd:string[2]" xsi:type="soapenc:Array">'
            f'<item xsi:type="xsd:string">{html.escape(login)}</item>'
            f'<item xsi:type="xsd:string">{html.escape(password)}</item>'
            "</UserParam>"
        )

    return (
        "<UserParam>"
        f"<string>{html.escape(login)}</string>"
        f"<string>{html.escape(password)}</string>"
        "</UserParam>"
    )


def _envelope(oems_xml: str, user_xml: str, method_ns: str) -> str:
    if method_ns == "wsdl":
        open_tag = (
            '<ns1:getPriceByOem '
            'xmlns:ns1="https://automototrade.com/wsdl/server.php">'
        )
        close_tag = "</ns1:getPriceByOem>"
    else:
        open_tag = "<getPriceByOem>"
        close_tag = "</getPriceByOem>"

    return f"""<?xml version="1.0" encoding="windows-1251"?>
<soapenv:Envelope
  xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/">
  <soapenv:Body>
    {open_tag}
      {oems_xml}
      {user_xml}
    {close_tag}
  </soapenv:Body>
</soapenv:Envelope>"""


def _headers_soap11(action: str) -> dict[str, str]:
    return {
        "Content-Type": "text/xml; charset=windows-1251",
        "SOAPAction": action,
        "Accept": "text/xml",
        "Connection": "close",
    }


def _post(xml: str, headers: dict[str, str]) -> str:
    response = requests.post(
        settings.AMT_ENDPOINT,
        data=xml.encode("cp1251", errors="replace"),
        headers=headers,
        timeout=settings.AMT_TIMEOUT_SECONDS,
        verify=settings.AMT_VERIFY_SSL,
    )
    response.raise_for_status()
    return response.text


def _as_text(value: Any) -> Any:
    if isinstance(value, dict) and "#text" in value:
        return value["#text"]

    return value


def _is_apache_map(node: Any) -> bool:
    if not isinstance(node, dict):
        return False

    for key in node.keys():
        if key.endswith(":Map") or key == "Map":
            return True

    node_type = node.get("@xsi:type") or node.get("xsi:type") or node.get("@type")

    return isinstance(node_type, str) and node_type.endswith(":Map")


def _apache_map_to_dict(node: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(node.items()):
        if key.endswith(":Map") or key == "Map":
            node = value
            break

    items = node.get("item")

    if not items:
        return {}

    if not isinstance(items, list):
        items = [items]

    output: dict[str, Any] = {}

    for item in items:
        if not isinstance(item, dict):
            continue

        key = _as_text(item.get("key"))
        value = _as_text(item.get("value"))

        if key is None:
            continue

        output[str(key)] = value

    return output


def _looks_like_row(data: dict[str, Any]) -> bool:
    keys = {key.split(":")[-1] for key in data.keys()}
    return bool(keys & {"oem", "oem_original", "supplier", "brand", "descr"})


def _normalize_numbers(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        for field_name in ("weight", "list_price", "core_price"):
            value = row.get(field_name)

            if value in ("", None):
                row[field_name] = None
                continue

            try:
                row[field_name] = float(value)
            except Exception:
                row[field_name] = None


def _extract_rows(obj: Any) -> list[dict[str, Any]] | None:
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and _looks_like_row(obj[0]):
            return [
                {key.split(":")[-1]: value for key, value in row.items()}
                for row in obj
            ]

        if obj and isinstance(obj[0], dict) and _is_apache_map(obj[0]):
            rows = []

            for item in obj:
                row = _apache_map_to_dict(item)

                if row:
                    rows.append(
                        {
                            key.split(":")[-1]: _as_text(value)
                            for key, value in row.items()
                        }
                    )

            if rows:
                return rows

        for item in obj:
            found = _extract_rows(item)

            if found:
                return found

    if isinstance(obj, dict):
        if _is_apache_map(obj):
            row = _apache_map_to_dict(obj)

            if row:
                return [
                    {
                        key.split(":")[-1]: _as_text(value)
                        for key, value in row.items()
                    }
                ]

        for value in obj.values():
            found = _extract_rows(value)

            if found:
                return found

    return None


def get_price_by_oem(oems: list[str] | str) -> list[dict[str, Any]]:
    login = settings.AMT_API_LOGIN
    password = settings.AMT_API_PASSWORD

    if not login or not password:
        raise AMTProviderError("AMT credentials are not configured")

    if isinstance(oems, str):
        oems = [oems]

    if not settings.AMT_VERIFY_SSL:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    actions = [
        "",
        "getPriceByOem",
        "https://automototrade.com/wsdl/server.php#getPriceByOem",
    ]
    oem_modes = ["enc", "simple"]
    user_modes = ["struct", "enc", "simple"]
    method_spaces = ["wsdl", "plain"]

    last_response = None

    for action in actions:
        for oem_mode in oem_modes:
            for user_mode in user_modes:
                for method_ns in method_spaces:
                    oems_xml = _oem_array_xml(oems, oem_mode)
                    user_xml = _userparam_xml(login, password, user_mode)
                    envelope = _envelope(oems_xml, user_xml, method_ns)
                    headers = _headers_soap11(action)

                    try:
                        text = _post(envelope, headers)
                        last_response = text

                        if (
                            "Incorrect parametr" in text
                            or "Incorrect parameters" in text
                        ):
                            raise AMTProviderError(
                                "AMT returned incorrect parameters"
                            )

                        document = xmltodict.parse(text)
                        rows = _extract_rows(document)

                        if rows:
                            _normalize_numbers(rows)
                            return rows

                    except AMTProviderError:
                        raise
                    except (
                        requests.HTTPError,
                        requests.ConnectionError,
                        requests.Timeout,
                    ):
                        continue

    if last_response:
        preview = last_response[:400].replace("\r", "").replace("\n", " ")
        raise AMTProviderError(f"Could not parse AMT response: {preview}")

    raise AMTProviderError("Could not get AMT response")