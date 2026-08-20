import logging

import requests
from bs4 import BeautifulSoup

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

BCV_URL = 'https://www.bcv.org.ve/'

# Mapa: código ISO → id del div en la página del BCV
BCV_CURRENCIES = {
    'USD': 'dolar',
    'EUR': 'euro',
}


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    # -------------------------------------------------------------------------
    # Punto de entrada: botón en formulario
    # -------------------------------------------------------------------------
    def action_update_bcv_rates_manual(self):
        return self.env['res.currency'].action_update_bcv_rates(manual=True)

    # -------------------------------------------------------------------------
    # Punto de entrada: cron y server action
    # -------------------------------------------------------------------------
    @api.model
    def action_update_bcv_rates(self, manual=False):
        try:
            rates = self._fetch_bcv_rates()
        except Exception as e:
            msg = 'Error al consultar el BCV: %s' % e
            _logger.error(msg)
            if manual:
                raise UserError(msg)
            return False

        company = self.env.company
        today = fields.Date.today()

        if company.currency_id.name != 'VES':
            _logger.warning(
                've_bcv_rates asume moneda de la compañía = VES (bug real '
                'corregido 2026-07-28: antes se guardaba el valor del BCV '
                'sin invertir) — la compañía %s tiene %s, las tasas '
                'guardadas van a quedar mal.', company.name, company.currency_id.name,
            )

        for code, rate_value in rates.items():
            currency = self.search([('name', '=', code)], limit=1)
            if not currency:
                _logger.warning('Moneda %s no encontrada en Odoo — se omite', code)
                continue

            # BCV publica Bs por 1 unidad de moneda extranjera (ej. 742.81
            # Bs/USD) — Odoo espera lo inverso en res.currency.rate.rate
            # cuando la moneda de la compañía es VES: "unidades de esta
            # moneda por 1 unidad de la moneda de la compañía" (1 VES =
            # 1/742.81 USD). Bug real encontrado 2026-07-28: se guardaba
            # el valor del BCV directo sin invertir desde ~julio — rompía
            # cualquier conversión de moneda del sistema (Sanciones IVA en
            # EUR, reportes en USD, etc.) por un factor ~rate².
            odoo_rate = (1.0 / rate_value) if rate_value else 0.0

            existing = self.env['res.currency.rate'].search([
                ('currency_id', '=', currency.id),
                ('name', '=', today),
                ('company_id', '=', company.id),
            ], limit=1)

            if existing:
                existing.rate = odoo_rate
            else:
                self.env['res.currency.rate'].create({
                    'currency_id': currency.id,
                    'name': today,
                    'rate': odoo_rate,
                    'company_id': company.id,
                })

            _logger.info('Tasa BCV actualizada — %s: %s Bs', code, rate_value)

        if manual:
            lines = ['%s: %s' % (code, '{:,.5f}'.format(val)) for code, val in rates.items()]
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Tasas BCV actualizadas',
                    'message': ' | '.join(lines),
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    # -------------------------------------------------------------------------
    # Scraping del portal BCV
    # -------------------------------------------------------------------------
    @api.model
    def _fetch_bcv_rates(self):
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0 Safari/537.36'
            ),
            'Accept-Language': 'es-VE,es;q=0.9',
        }

        try:
            response = requests.get(BCV_URL, headers=headers, timeout=15, verify=False)
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError('No se pudo conectar al portal BCV: %s' % e)

        soup = BeautifulSoup(response.content, 'html.parser')
        rates = {}

        for code, div_id in BCV_CURRENCIES.items():
            div = soup.find('div', id=div_id)
            if not div:
                raise ValueError(
                    'Elemento "%s" no encontrado en la página del BCV. '
                    'Es posible que hayan cambiado el diseño del portal.' % div_id
                )

            strong = div.find('strong')
            if not strong or not strong.text.strip():
                raise ValueError('No se encontró el valor de tasa para %s en la página del BCV.' % code)

            # Formato venezolano: "36,50000" (coma = decimal, punto = miles)
            rate_text = strong.text.strip().replace('.', '').replace(',', '.')
            try:
                rates[code] = float(rate_text)
            except ValueError:
                raise ValueError(
                    'Valor de tasa para %s no es numérico: "%s"' % (code, strong.text.strip())
                )

        return rates
