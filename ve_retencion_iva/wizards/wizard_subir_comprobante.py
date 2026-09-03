import base64
import logging
import re
from io import BytesIO

from markupsafe import Markup, escape

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


_MESES_ES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12',
    'ene': '01', 'feb': '02', 'mar': '03', 'abr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'ago': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dic': '12',
}


def _normalizar_periodo(texto):
    """Convierte cualquier formato de período a yyyy-mm. Retorna '' si no reconoce."""
    if not texto:
        return ''
    texto = texto.strip()
    # yyyy-mm (canónico)
    m = re.match(r'^(\d{4})-(\d{1,2})$', texto)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # MM/YYYY o MM-YYYY
    m = re.match(r'^(\d{1,2})[/-](\d{4})$', texto)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    # YYYY/MM
    m = re.match(r'^(\d{4})[/-](\d{1,2})$', texto)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # MMYYYY (ej: 052026)
    m = re.match(r'^(\d{2})(\d{4})$', texto)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{m.group(1)}"
    # YYYYMM (ej: 202605)
    m = re.match(r'^(\d{4})(\d{2})$', texto)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    # Nombre de mes: "Mayo 2026", "2026 Mayo", "mayo de 2026"
    for nombre, num in _MESES_ES.items():
        match = re.search(rf'\b{nombre}\b.*?(\d{{4}})', texto, re.I)
        if match:
            return f"{match.group(1)}-{num}"
        match = re.search(rf'(\d{{4}}).*?\b{nombre}\b', texto, re.I)
        if match:
            return f"{match.group(1)}-{num}"
    return ''


def _try_parse_date(s):
    """Convierte string de fecha a 'YYYY-MM-DD'. Retorna None si no reconoce."""
    if not s:
        return None
    from datetime import datetime
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%y'):
        try:
            return datetime.strptime(s.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def _parse_amount(s):
    """Convierte número venezolano (. miles, , decimal) a float."""
    s = re.sub(r'[^\d.,]', '', s.strip())
    if not s:
        return 0.0
    if ',' in s and '.' in s:
        # Formato venezolano: 8.976,58 → 8976.58
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        # Solo coma: puede ser decimal (1234,56) o miles (1,234)
        s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


class WizardSubirComprobante(models.TransientModel):
    _name = 've.wh.iva.wizard.comprobante'
    _description = 'Subir y Escanear Comprobante de Retención IVA'

    wh_iva_id = fields.Many2one('ve.wh.iva', required=True)
    archivo = fields.Binary(string='Imagen / PDF del comprobante')
    archivo_nombre = fields.Char(string='Nombre del archivo')

    texto_ocr = fields.Text(string='Detalle OCR', readonly=True)

    # Campos que se actualizan en el registro
    ocr_name = fields.Char(string='N° Comprobante')
    ocr_nro_control = fields.Char(string='N° Control')
    ocr_nro_documento = fields.Char(string='N° Factura')
    ocr_monto_base = fields.Float(string='Base Imponible', digits=(16, 2))
    ocr_monto_iva = fields.Float(string='Impuesto IVA', digits=(16, 2))
    ocr_monto_retenido = fields.Float(
        string='IVA Retenido (según comprobante)', digits=(16, 2),
        help='Monto retenido tal como aparece impreso en el comprobante — '
             'no recalculado. Es el que se compara contra el Monto Esperado '
             'para detectar diferencias.')

    # Campos OCR editables
    ocr_fecha = fields.Char(string='Fecha emisi\xf3n', readonly=True)  # Char existente — no cambiar tipo
    ocr_fecha_doc = fields.Date(string='Fecha Comprobante')             # Date editable para guardar
    ocr_tipo_documento = fields.Char(string='Tipo de Transacción')
    ocr_doc_afectado = fields.Char(
        string='Documento Afectado',
        help='N° de Factura/Control que este comprobante afecta -- solo '
             'aplica si Tipo de Transacción es Nota de Crédito/Débito '
             '(Art. 75/76 Reglamento LIVA). Agregado 2026-09-03.')
    ocr_alicuota = fields.Float(string='Alícuota %', digits=(5, 2), default=16.0)

    # Informativos (no se escriben al registro)
    ocr_periodo = fields.Char(string='Período fiscal', readonly=True)
    ocr_rif_agente = fields.Char(string='RIF')
    ocr_agente_nombre = fields.Char(string='Nombre Agente (OCR)', readonly=True)

    # Cliente identificado por OCR (editable para que el usuario pueda seleccionar/crear)
    cliente_id = fields.Many2one('res.partner', string='Cliente / Agente de Retención')
    cliente_no_encontrado = fields.Boolean(readonly=True)
    ocr_exitoso = fields.Boolean(readonly=True)

    # Advertencia de reemplazo
    tiene_comprobante = fields.Boolean(compute='_compute_tiene_comprobante')
    confirmar_reemplazo = fields.Boolean(
        string='Entendido, deseo reemplazar el comprobante existente')

    actualizar_campos = fields.Boolean(
        string='Actualizar campos de la retención con datos OCR',
        default=True,
    )

    @api.depends('wh_iva_id.estado_recepcion')
    def _compute_tiene_comprobante(self):
        # Depende de wh_iva_id.estado_recepcion (campo almacenado, ya resuelve
        # la excepción "Declarado sin Comprobante" internamente — ver
        # ve_wh_iva.py::_compute_estado_recepcion) en vez de duplicar aquí la
        # misma pregunta "¿está recibido?" con su propio set de states.
        for rec in self:
            rec.tiene_comprobante = rec.wh_iva_id.estado_recepcion in rec.wh_iva_id._RECIBIDO_ESTADOS

    # ------------------------------------------------------------------ #
    #  OCR                                                                 #
    # ------------------------------------------------------------------ #

    def _extraer_con_iap(self, archivo_b64):
        """Extrae datos vía OCR de Odoo IAP (créditos IAP). Retorna dict o None."""
        try:
            from odoo.addons.iap.tools import iap_tools
        except ImportError:
            _logger.warning('ve_retencion_iva OCR-IAP: módulo iap no disponible (ImportError)')
            return None

        try:
            account = self.env['iap.account'].get('invoice_ocr')
        except Exception as e:
            _logger.warning('ve_retencion_iva OCR-IAP: error al obtener iap.account: %s', e)
            return None

        if not account:
            _logger.warning('ve_retencion_iva OCR-IAP: iap.account.get("invoice_ocr") retornó None')
            return None

        if not account.account_token:
            _logger.warning(
                've_retencion_iva OCR-IAP: la cuenta invoice_ocr (id=%s) no tiene token. '
                'Instale el módulo "account_invoice_extract" y compre créditos IAP.',
                account.id,
            )
            return None

        _logger.info('ve_retencion_iva OCR-IAP: usando cuenta id=%s, token=%s…',
                     account.id, account.account_token[:8])

        b64_str = (archivo_b64.decode('utf-8')
                   if isinstance(archivo_b64, bytes) else archivo_b64)

        params = {
            'account_token': account.account_token,
            'version': 3,
            'documents': [b64_str],
        }

        try:
            result = iap_tools.iap_jsonrpc(
                self.env,
                'https://iap-extract.odoo.com/api/extract/invoice/2/parse',
                params=params,
                timeout=30,
            )
        except Exception as e:
            _logger.warning('ve_retencion_iva OCR-IAP: error en iap_jsonrpc: %s', e)
            return None

        _logger.info('ve_retencion_iva OCR-IAP: respuesta recibida: %s',
                     str(result)[:300])

        if not isinstance(result, dict):
            _logger.warning('ve_retencion_iva OCR-IAP: respuesta no es dict: %s', type(result))
            return None

        if result.get('status') != 'success':
            _logger.warning('ve_retencion_iva OCR-IAP: status=%s, respuesta=%s',
                            result.get('status'), str(result)[:300])
            return None

        try:
            data = result['results'][0]
        except (KeyError, IndexError) as e:
            _logger.warning('ve_retencion_iva OCR-IAP: no se pudo extraer results[0]: %s', e)
            return None

        def get_val(field):
            try:
                return data[field]['selected_value']['content']
            except (KeyError, TypeError):
                return None

        vals = {}

        nro = get_val('invoice_id')
        if nro:
            vals['ocr_name'] = str(nro).strip()

        fecha = get_val('invoice_date')
        if fecha:
            vals['ocr_fecha'] = str(fecha).strip()

        rif = get_val('VAT_Number')
        if rif:
            vals['ocr_rif_agente'] = str(rif).strip()

        subtotal = get_val('subtotal')
        if subtotal is not None:
            try:
                vals['ocr_monto_base'] = float(subtotal)
            except (ValueError, TypeError):
                pass

        tax_amount = get_val('global_taxes_amount')
        if tax_amount is not None:
            try:
                vals['ocr_monto_iva'] = float(tax_amount)
            except (ValueError, TypeError):
                pass

        _logger.info('ve_retencion_iva OCR-IAP: campos extraídos: %s', list(vals.keys()))
        vals['texto_ocr'] = 'Extraído vía Odoo IAP OCR'
        return vals if len(vals) > 1 else None

    def _extraer_texto(self, archivo_b64):
        """Devuelve el texto extraído de la imagen o None si OCR no está disponible."""
        import os
        import platform

        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return None

        # En Windows, buscar tesseract.exe automáticamente
        if platform.system() == 'Windows':
            param_path = self.env['ir.config_parameter'].sudo().get_param(
                've_retencion_iva.tesseract_path', ''
            )
            candidates = [
                param_path,
                r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                r'C:\Users\susan\AppData\Local\Programs\Tesseract-OCR\tesseract.exe',
            ]
            for path in candidates:
                if path and os.path.isfile(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break
            else:
                return None

        try:
            data = base64.b64decode(archivo_b64)
            img = Image.open(BytesIO(data))
            img = img.convert('L')
            try:
                return pytesseract.image_to_string(img, lang='spa')
            except Exception:
                return pytesseract.image_to_string(img)
        except Exception:
            return None

    def _parsear_comprobante(self, texto):
        """Extrae campos del texto OCR de un comprobante de retención IVA."""
        vals = {}

        # ── N° Comprobante ──────────────────────────────────────────────
        # Etiquetas: "NRO. COMPROBANTE", "Nro. Comprobante", "NRO. DE COMPROBANTE"
        m = re.search(
            r'(?:NRO\.?\s*(?:DE\s*)?COMPROBANTE|Nro\.?\s*(?:de\s*)?Comprobante)'
            r'\s*[:\n]?\s*([\d][\d\-]{8,})',
            texto, re.I,
        )
        if m:
            vals['ocr_name'] = m.group(1).strip()

        # ── Fecha de Emisión ─────────────────────────────────────────────
        m = re.search(
            r'(?:FECHA\s*(?:DE\s*EMISI[OÓo]N)?|Fecha\s*(?:de\s*Emisi[oó]n)?)'
            r'\s*[:\n]?\s*(\d{1,2}/\d{1,2}/\d{4})',
            texto, re.I,
        )
        if not m:
            m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', texto)
        if m:
            raw_fecha = m.group(1).strip()
            vals['ocr_fecha'] = raw_fecha
            parsed_date = _try_parse_date(raw_fecha)
            if parsed_date:
                vals['ocr_fecha_doc'] = parsed_date

        # ── Tipo de Transacción ──────────────────────────────────────────
        m = re.search(
            r'(?:TIPO\s*(?:DE\s*)?(?:DOCUMENTO|TRANSACCI[OÓo]N|DOC\.?))'
            r'\s*[:\n]?\s*([A-Za-záéíóúÁÉÍÓÚñÑ][^\n]{2,40})',
            texto, re.I,
        )
        if m:
            vals['ocr_tipo_documento'] = m.group(1).strip()[:50]

        # ── Documento Afectado (Art. 75/76 Reglamento LIVA) ─────────────
        m = re.search(
            r'(?:DOCUMENTO\s*AFECTADO|FACTURA\s*AFECTADA)'
            r'\s*[:\n]?\s*([A-Za-z0-9][^\n]{0,40})',
            texto, re.I,
        )
        if m:
            vals['ocr_doc_afectado'] = m.group(1).strip()[:50]

        # ── Alícuota ─────────────────────────────────────────────────────
        m = re.search(r'(?:AL[IÍ]CUOTA|ALICUOTA)\s*[:%]?\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%?', texto, re.I)
        if m:
            try:
                vals['ocr_alicuota'] = float(m.group(1).replace(',', '.'))
            except ValueError:
                pass

        # ── Período Fiscal ───────────────────────────────────────────────
        m = re.search(
            r'PERIODO\s*FISCAL\s*[:\n]?\s*([\d]{4}\s*[-/]\s*[\d]{2}|[\d]{6}|[\d]{2}/[\d]{4})',
            texto, re.I,
        )
        if m:
            raw_p = m.group(1).strip()
            vals['ocr_periodo'] = _normalizar_periodo(raw_p) or raw_p

        # ── RIFs (formato venezolano: J-XXXXXXXX-X) ──────────────────────
        # El primero = Agente de Retención (quien emite el comprobante)
        rifs = re.findall(r'[JGVEPjgvep]-?\d{7,9}-?\d', texto)
        if rifs:
            vals['ocr_rif_agente'] = rifs[0]

        # ── N° Control (00-XXXXXX) ───────────────────────────────────────
        m = re.search(r'\b(0\d-\d{5,7})\b', texto)
        if m:
            vals['ocr_nro_control'] = m.group(1).strip()

        # ── N° Factura ───────────────────────────────────────────────────
        # Busca número de 9+ dígitos cerca de la etiqueta de factura
        m = re.search(
            r'(?:N[uú]m(?:ero)?\s*(?:de\s*)?(?:Factura|Comprobante))'
            r'\s*[:\n \t]*(\d{6,})',
            texto, re.I,
        )
        if not m:
            # Fallback: primer número de 9 dígitos que no sea el comprobante ya extraído
            nro_comp = vals.get('ocr_name', '')
            for hit in re.finditer(r'\b(\d{9})\b', texto):
                if hit.group(1) not in nro_comp:
                    m = hit
                    break
        if m:
            vals['ocr_nro_documento'] = m.group(1).strip()

        # ── Base Imponible ───────────────────────────────────────────────
        # En la línea de TOTALES, antes del % alícuota
        m = re.search(
            r'(?:TOTALES?|Totales?)\s+[\d.,\s]*?\s+([\d.,]{4,})\s+(?:16|8|%)',
            texto, re.I,
        )
        if not m:
            m = re.search(r'Base\s*Imponible[^\d]*([\d.,]{4,})', texto, re.I)
        if m:
            val = _parse_amount(m.group(1))
            if val > 0:
                vals['ocr_monto_base'] = val

        # ── Impuesto IVA / Impuesto Causado ──────────────────────────────
        m = re.search(
            r'(?:Impuesto\s*(?:IVA|Causado|del\s*IVA)|Total\s*IVA)'
            r'[^\d]*([\d.,]{3,})',
            texto, re.I,
        )
        if m:
            val = _parse_amount(m.group(1))
            if val > 0:
                vals['ocr_monto_iva'] = val

        return vals

    def _extraer_con_claude_vision(self, archivo_b64):
        """Extrae campos del comprobante usando Claude Vision. Lanza Exception si hay error."""
        import json
        import urllib.request
        import urllib.error

        api_key = self.env['ir.config_parameter'].sudo().get_param(
            've_retencion_iva.anthropic_api_key', ''
        ).strip()
        if not api_key:
            return None

        raw = base64.b64decode(archivo_b64)
        b64_str = base64.b64encode(raw).decode('utf-8')

        if raw[:4] == b'%PDF':
            content_block = {
                'type': 'document',
                'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64_str},
            }
        else:
            media_type = (
                'image/png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'image/jpeg'
            )
            content_block = {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': media_type, 'data': b64_str},
            }

        prompt = (
            'Analiza este comprobante de retención IVA venezolano y extrae los datos. '
            'Responde ÚNICAMENTE con un objeto JSON con estos campos '
            '(usa null si el campo no aparece en el documento):\n'
            '{\n'
            '  "nro_comprobante": "número del comprobante de retención (14 dígitos, formato aaaaMM + 8 consecutivo)",\n'
            '  "fecha_emision": "DD/MM/YYYY",\n'
            '  "tipo_transaccion": "tipo de documento o transacción (ej: Factura, Nota de Crédito)",\n'
            '  "documento_afectado": "N° de Factura o Control que este documento afecta -- '
            'solo si es Nota de Crédito/Débito, null si es Factura regular",\n'
            '  "periodo_fiscal": "período fiscal en formato yyyy-mm (ej: 2026-05). '
            'El agente puede escribirlo como 05/2026, Mayo 2026, 2026-05, etc.",\n'
            '  "agente_nombre": "nombre o razón social del agente de retención",\n'
            '  "agente_rif": "RIF del agente",\n'
            '  "sujeto_nombre": "nombre del sujeto retenido",\n'
            '  "nro_factura": "número de factura (solo dígitos)",\n'
            '  "nro_control": "número de control formato 00-XXXXXXX",\n'
            '  "total_con_iva": número,\n'
            '  "compras_exentas": número,\n'
            '  "base_imponible": número,\n'
            '  "alicuota": número,\n'
            '  "impuesto_causado": número,\n'
            '  "iva_retenido": número,\n'
            '  "total_a_pagar": número\n'
            '}\n'
            'Los montos son decimales. Formato venezolano: 8.976,58 = 8976.58'
        )

        payload = json.dumps({
            'model': 'claude-sonnet-4-6',
            'max_tokens': 1024,
            'messages': [{'role': 'user', 'content': [
                content_block, {'type': 'text', 'text': prompt},
            ]}],
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode('utf-8', errors='replace')
            raise Exception(f'HTTP {e.code}: {body}')
        except Exception as e:
            raise Exception(f'conexión a api.anthropic.com: {e}')

        # Error de API (autenticación, rate limit, etc.)
        if result.get('type') == 'error':
            err = result.get('error', {})
            raise Exception(f"{err.get('type', 'api_error')}: {err.get('message', str(result))}")

        try:
            text = result['content'][0]['text'].strip()
            if '```' in text:
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            data = json.loads(text)
        except (KeyError, IndexError):
            raise Exception(f'respuesta inesperada: {str(result)[:200]}')
        except json.JSONDecodeError as e:
            raise Exception(f'JSON inválido en respuesta: {e}')

        vals = {}
        if data.get('nro_comprobante'):
            vals['ocr_name'] = str(data['nro_comprobante']).strip()
        if data.get('fecha_emision'):
            raw_fecha = str(data['fecha_emision']).strip()
            vals['ocr_fecha'] = raw_fecha
            parsed_date = _try_parse_date(raw_fecha)
            if parsed_date:
                vals['ocr_fecha_doc'] = parsed_date
        if data.get('tipo_transaccion'):
            vals['ocr_tipo_documento'] = str(data['tipo_transaccion']).strip()[:50]
        if data.get('documento_afectado'):
            vals['ocr_doc_afectado'] = str(data['documento_afectado']).strip()[:50]
        if data.get('periodo_fiscal'):
            raw_p = str(data['periodo_fiscal']).strip()
            vals['ocr_periodo'] = _normalizar_periodo(raw_p) or raw_p
        if data.get('agente_rif'):
            vals['ocr_rif_agente'] = str(data['agente_rif']).strip()
        if data.get('agente_nombre'):
            vals['ocr_agente_nombre'] = str(data['agente_nombre']).strip()
        if data.get('nro_control'):
            vals['ocr_nro_control'] = str(data['nro_control']).strip()
        if data.get('nro_factura'):
            vals['ocr_nro_documento'] = str(data['nro_factura']).strip()
        for src, dst in [
            ('base_imponible', 'ocr_monto_base'),
            ('impuesto_causado', 'ocr_monto_iva'),
            ('alicuota', 'ocr_alicuota'),
            ('iva_retenido', 'ocr_monto_retenido'),
        ]:
            try:
                v = data.get(src)
                if v is not None:
                    vals[dst] = float(v)
            except (ValueError, TypeError):
                pass

        resumen = ['Claude Vision AI', '']
        labels = {
            'nro_comprobante': 'N° Comprobante', 'fecha_emision': 'Fecha',
            'periodo_fiscal': 'Período', 'agente_nombre': 'Agente',
            'agente_rif': 'RIF', 'sujeto_nombre': 'Sujeto',
            'nro_factura': 'N° Factura', 'nro_control': 'N° Control',
            'total_con_iva': 'Total c/IVA', 'base_imponible': 'Base Imponible',
            'alicuota': 'Alícuota %', 'impuesto_causado': 'Impuesto',
            'iva_retenido': 'IVA Retenido', 'total_a_pagar': 'Total a Pagar',
        }
        for k, label in labels.items():
            if data.get(k) is not None:
                resumen.append(f'{label}: {data[k]}')
        vals['texto_ocr'] = '\n'.join(resumen)

        _logger.info('ve_retencion_iva OCR-Claude: %s', list(vals.keys()))
        return vals if len(vals) > 1 else None

    def _extraer_con_google_vision(self, archivo_b64):
        """Extrae texto vía Google Cloud Vision API. Retorna dict o None."""
        import json
        import urllib.request
        import urllib.error

        api_key = self.env['ir.config_parameter'].sudo().get_param(
            've_retencion_iva.google_vision_api_key', ''
        ).strip()
        if not api_key:
            _logger.info('ve_retencion_iva OCR-Vision: parámetro google_vision_api_key no configurado')
            return None

        # Odoo almacena binarios como base64 con saltos de línea; Google Vision necesita base64 limpio
        raw = base64.b64decode(archivo_b64)
        b64_str = base64.b64encode(raw).decode('utf-8')

        payload = json.dumps({
            'requests': [{
                'image': {'content': b64_str},
                'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}],
                'imageContext': {'languageHints': ['es']},
            }]
        }).encode('utf-8')

        url = f'https://vision.googleapis.com/v1/images:annotate?key={api_key}'
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode('utf-8', errors='replace')
            raise Exception(f'HTTP {e.code}: {body}')
        except Exception as e:
            raise Exception(f'conexión a vision.googleapis.com: {e}')

        try:
            resp0 = result['responses'][0]
            if 'error' in resp0:
                raise Exception(f"API error: {resp0['error'].get('message', str(resp0['error']))}")
            texto = resp0['fullTextAnnotation']['text']
        except Exception as e:
            raise Exception(str(e) if 'API error' in str(e) else f'sin texto en respuesta: {str(result)[:200]}')

        _logger.info('ve_retencion_iva OCR-Vision: texto extraído (%d chars)', len(texto))
        parsed = self._parsear_comprobante(texto)
        parsed['texto_ocr'] = texto
        return parsed

    # ------------------------------------------------------------------ #
    #  Acciones                                                            #
    # ------------------------------------------------------------------ #

    def _buscar_cliente_por_rif(self, rif):
        """Busca res.partner por RIF/VAT. Retorna partner o None."""
        if not rif:
            return None
        rif_clean = rif.replace('-', '').replace(' ', '').upper()
        partner = self.env['res.partner'].search(
            [('vat', '!=', False)], limit=0
        ).filtered(
            lambda p: p.vat and p.vat.replace('-', '').replace(' ', '').upper() == rif_clean
        )
        return partner[:1] if partner else None

    def action_crear_cliente(self):
        """Abre formulario de nuevo partner con datos del agente pre-cargados."""
        self.ensure_one()
        return {
            'name': 'Crear Cliente',
            'type': 'ir.actions.act_window',
            'res_model': 'res.partner',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_name': self.ocr_agente_nombre or '',
                'default_vat': self.ocr_rif_agente or '',
                'default_customer_rank': 1,
            },
        }

    def action_escanear(self):
        """Intenta OCR con los backends disponibles: Claude → Google → IAP → Tesseract."""
        self.ensure_one()
        if not self.archivo:
            raise UserError(
                'Adjunte primero el archivo del comprobante '
                'y luego haga clic en Escanear.'
            )

        vals = None
        errors = []

        # 1. Claude Vision
        try:
            vals = self._extraer_con_claude_vision(self.archivo)
            if vals:
                vals['ocr_exitoso'] = True
        except Exception as e:
            errors.append(f'Claude Vision: {e}')
            _logger.warning('ve_retencion_iva OCR: Claude Vision — %s', e)

        # 2. Google Vision
        if not vals:
            try:
                vals = self._extraer_con_google_vision(self.archivo)
                if vals:
                    vals['ocr_exitoso'] = True
            except Exception as e:
                errors.append(f'Google Vision: {e}')
                _logger.warning('ve_retencion_iva OCR: Google Vision — %s', e)

        # 3. Odoo IAP
        if not vals:
            try:
                vals = self._extraer_con_iap(self.archivo)
                if vals:
                    vals['ocr_exitoso'] = True
            except Exception as e:
                errors.append(f'Odoo IAP: {e}')

        # 4. Tesseract local
        if not vals:
            texto = self._extraer_texto(self.archivo)
            if texto:
                parsed = self._parsear_comprobante(texto)
                if parsed:
                    parsed['ocr_exitoso'] = True
                    vals = parsed

        if not vals:
            msg = 'OCR no pudo extraer datos del comprobante.\n'
            if errors:
                msg += 'Detalles:\n' + '\n'.join(f'  • {e}' for e in errors) + '\n\n'
            msg += (
                'Para activar OCR configure en Ajustes → Parámetros del sistema:\n'
                '  • ve_retencion_iva.anthropic_api_key\n'
                '  • ve_retencion_iva.google_vision_api_key\n\n'
                'Ingrese los datos manualmente.'
            )
            vals = {'texto_ocr': msg, 'ocr_exitoso': False}

        # Buscar partner por RIF extraído
        if vals.get('ocr_rif_agente'):
            partner = self._buscar_cliente_por_rif(vals['ocr_rif_agente'])
            if partner:
                vals['cliente_id'] = partner.id
                vals['cliente_no_encontrado'] = False
            else:
                vals['cliente_no_encontrado'] = True

        self.write(vals)
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _validar_coincidencia(self, record):
        """Valida que el RIF y N° Control del comprobante coincidan con la retención.
        Lanza UserError (bloquea todo) si hay discrepancia.

        Antes se validaba contra N° Factura, pero ese dato es frágil (formato
        libre, no siempre coincide entre lo impreso en el comprobante y lo
        registrado en Odoo). N° Control es el mismo criterio que ya usa el
        matching automático del buzón (ve_comprobante_inbox._do_procesar) y es
        el campo que efectivamente identifica la retención de forma única.
        """
        errores = []

        # Validar RIF
        rif_comp = re.sub(r'[\s\-]', '', (self.ocr_rif_agente or '')).upper()
        rif_esp  = re.sub(r'[\s\-]', '', (record.partner_id.vat or '')).upper()
        if rif_comp and rif_esp and rif_comp != rif_esp:
            errores.append(
                f'RIF del comprobante: {self.ocr_rif_agente}\n'
                f'RIF esperado (retención): {record.partner_id.vat}'
            )

        # Validar N° Control (mismo criterio que el matching automático del buzón)
        ctrl_comp = re.sub(r'[\s\-]', '', (self.ocr_nro_control or '')).upper()
        ctrl_esp  = re.sub(r'[\s\-]', '', (record.nro_control or '')).upper()
        if ctrl_comp and ctrl_esp and ctrl_comp != ctrl_esp:
            errores.append(
                f'N° Control del comprobante: {self.ocr_nro_control}\n'
                f'N° Control esperado (retención): {record.nro_control}'
            )

        if errores:
            raise UserError(
                'El comprobante NO corresponde a esta retención.\n'
                'No se actualizó ningún dato.\n\n'
                + '\n\n'.join(errores)
            )

    def action_guardar(self):
        self.ensure_one()
        record = self.wh_iva_id

        # Ya existe un comprobante recibido — exigir confirmación explícita de reemplazo
        if self.tiene_comprobante and not self.confirmar_reemplazo:
            raise UserError(
                'Este comprobante ya tiene un recibo cargado.\n'
                'Marque "Confirmar reemplazo" si desea sobrescribirlo.'
            )

        # Validar que N° Comprobante esté presente
        if not self.ocr_name and not record.name:
            raise UserError(
                'El N° Comprobante es obligatorio.\n'
                'Ingréselo en el campo "N° Comprobante".'
            )

        # Validar coincidencia de RIF y N° Factura ANTES de actualizar nada
        self._validar_coincidencia(record)

        # Adjuntar archivo si se proporcionó
        if self.archivo:
            attachment = self.env['ir.attachment'].create({
                'name': self.archivo_nombre or 'comprobante_retencion',
                'datas': self.archivo,
                'res_model': 've.wh.iva',
                'res_id': record.id,
            })
            record.message_post(body='Comprobante adjuntado.', attachment_ids=[attachment.id])

        # Canal de recepción — "manual" siempre que no venga ya seteado, sin
        # importar el estado del registro. Antes solo se asignaba dentro del
        # bloque de auto-transición esperado/vencido→borrador, así que una
        # retención ya en 'declarado' (caso "Declarado sin Comprobante", el
        # comprobante llega tarde) nunca quedaba con el Canal actualizado.
        if not record.canal_recepcion:
            record.canal_recepcion = 'manual'

        # Auto-transición a Recibido
        if record.state in ('esperado', 'vencido'):
            record.write({
                'state': 'borrador',
                'fecha': record.fecha or fields.Date.today(),
            })
            record.message_post(
                body='Estado cambiado a Recibido al adjuntar comprobante.',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

        # Actualizar campos de identificación del comprobante
        # NOTA: ocr_fecha_doc es la fecha de EMISIÓN del comprobante (leída
        # del documento) — no debe escribirse en 'fecha' (Fecha Recibido).
        # 'fecha' ya quedó fijada arriba (línea ~801) al pasar a Recibido, y
        # _compute_fuera_plazo la usa para el chequeo de plazo legal; pisarla
        # con la fecha de emisión del papel produce fechas incorrectas y
        # rompe ese chequeo (bug reportado 2026-07-24).
        update = {}
        if self.ocr_name:
            update['name'] = self.ocr_name
        if self.ocr_tipo_documento:
            update['tipo_documento'] = self.ocr_tipo_documento
        if self.ocr_doc_afectado:
            update['doc_afectado'] = self.ocr_doc_afectado
        if self.ocr_nro_documento:
            update['nro_documento'] = self.ocr_nro_documento
        if self.ocr_nro_control:
            update['nro_control'] = self.ocr_nro_control

        # Montos Según Comprobante — antes no se escribían nunca (el registro
        # quedaba marcado Recibido pero sin datos de monto para comparar).
        comp_monto_retenido = 0.0
        if self.ocr_monto_base or self.ocr_monto_iva:
            es_alicuota_8 = abs(self.ocr_alicuota - 8.0) < abs(self.ocr_alicuota - 16.0)
            if es_alicuota_8:
                update['comp_base_8'] = self.ocr_monto_base
                update['comp_iva_8'] = self.ocr_monto_iva
            else:
                update['comp_base_16'] = self.ocr_monto_base
                update['comp_iva_16'] = self.ocr_monto_iva

        # comp_monto_retenido: usar el monto RETENIDO tal como está impreso
        # en el comprobante (self.ocr_monto_retenido, leído del campo
        # "iva_retenido" del OCR) — antes se RECALCULABA desde
        # ocr_monto_iva × nuestro % de retención, lo que nunca podía
        # detectar una diferencia real (siempre "cuadraba" consigo mismo,
        # sin importar qué monto retenido dijera realmente el papel). Bug
        # real reportado en vivo: diferencia visible en la imagen del
        # comprobante que la auditoría no detectaba ni avisaba.
        if self.ocr_monto_retenido:
            comp_monto_retenido = self.ocr_monto_retenido
            update['comp_monto_retenido'] = comp_monto_retenido
        elif self.ocr_monto_iva:
            # Fallback si el documento no trae el monto retenido explícito
            # (solo base + IVA causado): se recalcula como antes.
            comp_monto_retenido = round(
                self.ocr_monto_iva * record.porcentaje_retencion / 100, 2)
            update['comp_monto_retenido'] = comp_monto_retenido

        if update:
            record.write(update)

        # Chatter: registrar lo leído del comprobante (mismo criterio que
        # ve.comprobante.inbox._aplicar_match para el Buzón — antes esta
        # carga manual no dejaba ningún rastro de los datos OCR, solo el
        # adjunto en sí).
        record.message_post(
            body=Markup(
                '<b>Comprobante cargado manualmente</b><br/>'
                'N&#176; Comprobante: <b>{name}</b><br/>'
                'N&#176; Control: <b>{ctrl}</b><br/>'
                'RIF Agente: <b>{rif}</b><br/>'
                'Fecha: <b>{fecha}</b><br/>'
                'Base Imponible: <b>{base:,.2f} Bs</b> | IVA: <b>{iva:,.2f} Bs</b> '
                '({alicuota:.0f}%)<br/>'
                'Monto Retenido ({origen}): <b>{monto:,.2f} Bs</b>'
            ).format(
                name=escape(self.ocr_name or record.name or '—'),
                ctrl=escape(self.ocr_nro_control or '—'),
                rif=escape(self.ocr_rif_agente or '—'),
                fecha=escape(str(self.ocr_fecha_doc or self.ocr_fecha or '—')),
                base=self.ocr_monto_base, iva=self.ocr_monto_iva,
                alicuota=self.ocr_alicuota, monto=comp_monto_retenido,
                origen='según comprobante' if self.ocr_monto_retenido else 'calculado',
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # Diferencia de monto vs. lo esperado por SmartIVA — antes solo se
        # detectaba/avisaba en el flujo automático del Buzón de Comprobantes
        # (ve_comprobante_inbox._post_diferencia_monto); la carga manual
        # guardaba comp_monto_retenido pero nunca comparaba ni avisaba.
        monto_esperado = record.monto_retenido
        if (comp_monto_retenido > 0 and monto_esperado > 0
                and abs(comp_monto_retenido - monto_esperado) > 0.01):
            record.message_post(
                body=Markup(
                    '&#x26A0;&#xFE0F; <b>Diferencia de monto detectada</b><br/>'
                    'Monto esperado (SmartIVA): <b>{esperado:,.2f} Bs</b><br/>'
                    'Monto en el comprobante: <b>{comp:,.2f} Bs</b><br/>'
                    'Diferencia: <b>{dif:,.2f} Bs</b>'
                ).format(
                    esperado=monto_esperado, comp=comp_monto_retenido,
                    dif=comp_monto_retenido - monto_esperado,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

        return {'type': 'ir.actions.act_window_close'}
