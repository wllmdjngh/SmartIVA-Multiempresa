# -*- coding: utf-8 -*-
"""Wizard OCR para cargar el Calendario SENIAT anual (C1, ver
[[project_pendientes_codigo_pre_piloto_vencement]] item 5) -- mismo patrón
de wizard_subir_comprobante.py (Claude Vision → revisión editable →
confirmar), pero para las 2 tablas de la Providencia Administrativa en vez
de un comprobante individual. Dato fiscal sensible: SIEMPRE pasa por
pantalla de revisión antes de escribir el calendario real, nunca auto-
confirma."""

import base64
import json
import logging
import urllib.error
import urllib.request

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WizardCalendarioSeniat(models.TransientModel):
    _name = 've.calendario.seniat.wizard'
    _description = 'Cargar Calendario SENIAT (OCR)'

    archivo = fields.Binary(string='Providencia (PDF/Imagen)')
    archivo_nombre = fields.Char(string='Nombre del Archivo')

    anio = fields.Integer(string='Año Calendario')
    providencia = fields.Char(string='N° Providencia')
    gaceta_oficial = fields.Char(string='N° Gaceta Oficial')
    fecha_gaceta = fields.Date(string='Fecha Gaceta/Providencia')

    linea_ids = fields.One2many(
        've.calendario.seniat.wizard.linea', 'wizard_id',
        string='Días Límite (revisar antes de confirmar)')
    texto_ocr = fields.Text(string='Detalle OCR', readonly=True)
    ocr_exitoso = fields.Boolean(default=False)

    def action_escanear(self):
        self.ensure_one()
        if not self.archivo:
            raise UserError('Adjunte primero el archivo de la Providencia.')

        vals, error = self._extraer_con_claude_vision(self.archivo)
        if error:
            self.texto_ocr = (
                f'OCR no pudo extraer el calendario.\n{error}\n\n'
                'Puede completar los datos manualmente en las líneas de '
                'abajo (use el botón "Agregar línea").'
            )
            return self._reabrir()

        self.write({
            'anio': vals.get('anio') or False,
            'providencia': vals.get('providencia') or False,
            'gaceta_oficial': vals.get('gaceta_oficial') or False,
            'fecha_gaceta': vals.get('fecha_gaceta') or False,
            'ocr_exitoso': True,
        })

        Linea = self.env['ve.calendario.seniat.wizard.linea']
        Linea.search([('wizard_id', '=', self.id)]).unlink()
        nuevas = []
        for tabla, quincena in (('tabla_a1', '1Q'), ('tabla_a2', '2Q')):
            filas = vals.get(tabla) or {}
            for digito_str, valores in filas.items():
                try:
                    digito = int(digito_str)
                except (TypeError, ValueError):
                    continue
                if not isinstance(valores, list) or len(valores) != 12:
                    continue
                for i, dia in enumerate(valores):
                    mes = i + 1
                    try:
                        dia_int = int(dia)
                    except (TypeError, ValueError):
                        continue
                    nuevas.append({
                        'wizard_id': self.id,
                        'quincena': quincena,
                        'mes': mes,
                        'rif_digito': digito,
                        'dia_limite': dia_int,
                    })
        Linea.create(nuevas)

        n = len(nuevas)
        self.texto_ocr = (
            f'Claude Vision AI — {n}/240 filas extraídas.\n'
            f'Año: {vals.get("anio")}\n'
            f'Providencia: {vals.get("providencia")}\n'
            f'Gaceta Oficial: {vals.get("gaceta_oficial")}\n'
            f'Fecha: {vals.get("fecha_gaceta")}\n\n'
            '⚠ REVISE cada fila contra el documento original antes de '
            'confirmar — es data fiscal de vencimientos, un dígito mal '
            'leído puede marcar como "a tiempo" una declaración vencida.'
            + ('' if n == 240 else f'\n\n⚠ Se esperaban 240 filas, se '
               f'extrajeron {n} — revise que no falte ninguna combinación '
               f'quincena/mes/dígito antes de confirmar.')
            + (f'\n\n⚠ Una de las 2 llamadas OCR falló: {vals["_error_parcial"]} '
               f'— complete a mano las filas de esa tabla.'
               if vals.get('_error_parcial') else '')
        )
        return self._reabrir()

    def _reabrir(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _extraer_con_claude_vision(self, archivo_b64):
        """Devuelve (vals, error). vals=None si error.

        Bug real reportado 2026-09-10 (primera versión, una sola llamada
        con las 2 tablas a la vez): la usuaria probó en vivo y tuvo que
        corregir "muchos" dígitos -- una tabla de 240 celdas de una sola
        pasada le da poco margen de atención por celda al modelo. Fix:
        2 llamadas separadas, una por tabla (120 celdas c/u) -- mismo
        criterio que "dividir para revisar mejor" ya usado en otras partes
        del módulo. El encabezado (año/providencia/gaceta/fecha) se pide en
        ambas llamadas por si alguna falla, se usa el primero que responda."""
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            've_retencion_iva.anthropic_api_key', ''
        ).strip()
        if not api_key:
            return None, (
                'Falta configurar el parámetro '
                've_retencion_iva.anthropic_api_key en Ajustes.')

        raw = base64.b64decode(archivo_b64)
        b64_str = base64.b64encode(raw).decode('utf-8')
        if raw[:4] == b'%PDF':
            content_block = {
                'type': 'document',
                'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64_str},
            }
        else:
            media_type = 'image/png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'image/jpeg'
            content_block = {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': media_type, 'data': b64_str},
            }

        vals = {'anio': None, 'providencia': None, 'gaceta_oficial': None,
                 'fecha_gaceta': None, 'tabla_a1': None, 'tabla_a2': None}
        errores = []
        for tabla_id, titulo in (
            ('tabla_a1', 'a.1) Entre los días 01 al 15 de cada mes'),
            ('tabla_a2', 'a.2) Entre los días 16 y el último de cada mes'),
        ):
            data, err = self._llamar_claude_tabla(content_block, api_key, tabla_id, titulo)
            if err:
                errores.append(f'{tabla_id}: {err}')
                continue
            for campo in ('anio', 'providencia', 'gaceta_oficial', 'fecha_gaceta'):
                if not vals.get(campo) and data.get(campo):
                    vals[campo] = data[campo]
            vals[tabla_id] = data.get(tabla_id)

        if not vals['tabla_a1'] and not vals['tabla_a2']:
            return None, ' | '.join(errores) or 'Sin respuesta del OCR.'

        raw_fecha = vals.get('fecha_gaceta')
        if raw_fecha:
            try:
                d, m, y = str(raw_fecha).strip().split('/')
                vals['fecha_gaceta'] = f'{y}-{int(m):02d}-{int(d):02d}'
            except (ValueError, AttributeError):
                vals['fecha_gaceta'] = None
        if errores:
            vals['_error_parcial'] = ' | '.join(errores)
        return vals, None

    def _llamar_claude_tabla(self, content_block, api_key, tabla_id, titulo):
        """Una llamada a Claude Vision enfocada en UNA sola tabla (10 filas
        x 12 columnas = 120 celdas) -- ver nota en _extraer_con_claude_vision
        sobre por qué se separó de una sola llamada con las 2 tablas."""
        prompt = (
            'Este documento es una Providencia Administrativa del SENIAT '
            '(Venezuela) que establece el Calendario de Sujetos Pasivos '
            'Especiales y Agentes de Retención para un año fiscal. Trae 2 '
            'tablas — SOLO necesito la tabla "' + titulo + '". Ignora la '
            'otra tabla por completo.\n\n'
            'Esa tabla tiene 10 filas (R.I.F 0 al 9, el último dígito del '
            'RIF) y 12 columnas (ENE a DIC, el mes del período). Cada '
            'celda es el día del mes (1-31, 1 o 2 dígitos) en que vence la '
            'obligación para ese dígito de RIF ese mes.\n\n'
            'Lee la tabla con mucho cuidado, fila por fila, columna por '
            'columna, verificando cada dígito antes de escribirlo — es '
            'data fiscal de vencimientos, la precisión importa más que la '
            'velocidad.\n\n'
            'Responde ÚNICAMENTE con este JSON exacto (usa null si un dato '
            'de encabezado no aparece en el documento; la tabla SIEMPRE '
            'debe traer las 10 filas × 12 columnas completas):\n'
            '{\n'
            '  "anio": año calendario que rige este documento (número, ej 2026),\n'
            '  "providencia": "N° de Providencia, ej SNAT/2025/000091",\n'
            '  "gaceta_oficial": "N° de Gaceta Oficial, ej 470.331",\n'
            '  "fecha_gaceta": "fecha de la providencia/gaceta en formato DD/MM/YYYY",\n'
            f'  "{tabla_id}": {{\n'
            '    "0": [12 números enteros, uno por mes ENE..DIC],\n'
            '    "1": [...], "2": [...], "3": [...], "4": [...], "5": [...],\n'
            '    "6": [...], "7": [...], "8": [...], "9": [...]\n'
            '  }\n'
            '}\n'
            'No incluyas texto fuera del JSON.'
        )

        payload = json.dumps({
            'model': 'claude-sonnet-4-6',
            'max_tokens': 3072,
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
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode('utf-8', errors='replace')
            return None, f'HTTP {e.code}: {body}'
        except Exception as e:
            return None, f'Conexión a api.anthropic.com: {e}'

        if result.get('type') == 'error':
            err = result.get('error', {})
            return None, f"{err.get('type', 'api_error')}: {err.get('message', str(result))}"

        try:
            text = result['content'][0]['text'].strip()
            if '```' in text:
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            data = json.loads(text)
        except (KeyError, IndexError):
            return None, f'Respuesta inesperada: {str(result)[:300]}'
        except json.JSONDecodeError as e:
            return None, f'JSON inválido en respuesta: {e}'

        return data, None

    def action_confirmar(self):
        self.ensure_one()
        if not self.linea_ids:
            raise UserError(
                'No hay filas para confirmar. Escanee un archivo primero o '
                'agregue las filas a mano.')
        if len(self.linea_ids) != 240:
            raise UserError(
                f'Se esperan 240 filas (2 quincenas × 12 meses × 10 '
                f'dígitos de RIF) — hay {len(self.linea_ids)}. Revise las '
                f'líneas antes de confirmar.')
        if not self.anio:
            raise UserError('El Año Calendario es obligatorio.')

        Calendario = self.env['ve.calendario.seniat']
        existente = Calendario.search([('anio', '=', self.anio)], limit=1)
        vals_header = {
            'anio': self.anio,
            'providencia': self.providencia,
            'gaceta_oficial': self.gaceta_oficial,
            'fecha_gaceta': self.fecha_gaceta,
            'archivo': self.archivo,
            'archivo_nombre': self.archivo_nombre,
            'estado': 'confirmado',
        }
        if existente:
            existente.linea_ids.unlink()
            existente.write(vals_header)
            calendario = existente
        else:
            calendario = Calendario.create(vals_header)

        self.env['ve.calendario.seniat.linea'].create([{
            'calendario_id': calendario.id,
            'quincena': l.quincena,
            'mes': l.mes,
            'rif_digito': l.rif_digito,
            'dia_limite': l.dia_limite,
        } for l in self.linea_ids])

        if self.archivo:
            calendario.message_post(
                body=f'Calendario {self.anio} confirmado — Providencia '
                     f'{self.providencia or "(sin dato)"}, Gaceta Oficial '
                     f'{self.gaceta_oficial or "(sin dato)"}.',
                attachments=[(self.archivo_nombre or f'calendario_{self.anio}.pdf',
                               base64.b64decode(self.archivo))],
            )
        else:
            calendario.message_post(
                body=f'Calendario {self.anio} confirmado (cargado sin '
                     f'archivo adjunto).')

        return {
            'type': 'ir.actions.act_window',
            'res_model': 've.calendario.seniat',
            'res_id': calendario.id,
            'view_mode': 'form',
            'target': 'current',
        }


class WizardCalendarioSeniatLinea(models.TransientModel):
    _name = 've.calendario.seniat.wizard.linea'
    _description = 'Calendario SENIAT (Wizard) — Día Límite por RIF/Mes/Quincena'
    _order = 'quincena, mes, rif_digito'

    wizard_id = fields.Many2one(
        've.calendario.seniat.wizard', required=True, ondelete='cascade')
    quincena = fields.Selection([
        ('1Q', 'Entre 01 y 15'),
        ('2Q', 'Entre 16 y fin de mes'),
    ], required=True)
    mes = fields.Integer(string='Mes del Período', required=True)
    rif_digito = fields.Integer(string='Último Dígito RIF', required=True)
    dia_limite = fields.Integer(string='Día Límite', required=True)
