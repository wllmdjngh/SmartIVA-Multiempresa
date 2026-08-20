import base64
import calendar
import csv
import io
import logging
from datetime import date as _date, timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ── Compañía sandbox del piloto — RIF real asignado 2026-07-26 ─────────────
# SIN guión a propósito (J003168793, no J-003168793) — así es como vive el
# campo `vat` en la compañía real, porque AET lo necesita sin guión. Un
# desfase de formato acá (con vs. sin guión) hace que la búsqueda por vat
# nunca encuentre la compañía existente y el wizard intente crear una
# "nueva" con el mismo nombre → choca con la restricción de nombre único
# de res.company (bug real, encontrado 2026-07-28).
_PILOTO_COMPANY_NAME = 'PILOTO SMARTIVA CONECTA CA'
_PILOTO_COMPANY_VAT  = 'J003168793'

# ── Clientes SPE del piloto (alimentan el Libro de Ventas) ────────────────
# El orden importa: _variar_escenarios_venta() usa la posición (1..5) para
# dejar cada uno en un estado distinto de ve.wh.iva, igual que la variedad
# que ya tiene el demo general (wizard_reset_demo.py) — Uno=No Recibido,
# Dos=Vencido, Tres=Recibido sin confirmar (doble alícuota 16%+8%),
# Cuatro=No Recibido con factura pagada, Cinco=Confirmado (match SENIAT
# exacto). Seis (8% puro) y Siete (Exento) solo alimentan variedad del
# Libro de Ventas — quedan en 'esperado' por defecto, sin escenario propio.
_PILOTO_CLIENTES = [
    ('CLIENTE PILOTO UNO CA',    'J-91000001-0', 75.0),
    ('CLIENTE PILOTO DOS CA',    'J-91000002-0', 75.0),
    ('CLIENTE PILOTO TRES CA',   'J-91000003-0', 75.0),
    ('CLIENTE PILOTO CUATRO CA', 'J-91000004-0', 100.0),
    ('CLIENTE PILOTO CINCO CA',  'J-91000005-0', 75.0),
    ('CLIENTE PILOTO SEIS CA',   'J-91000006-0', 75.0),
    ('CLIENTE PILOTO SIETE CA',  'J-91000007-0', 75.0),
]

# ── Proveedores del piloto (alimentan el Libro de Compras, formato SENIAT) ─
# Uno=Borrador, Dos=Enviado, Tres=Declarado — variedad de ve.wh.iva.prov.
_PILOTO_PROVEEDORES = [
    ('PROVEEDOR PILOTO UNO CA',   'J-92000001-0'),
    ('PROVEEDOR PILOTO DOS CA',   'J-92000002-0'),
    ('PROVEEDOR PILOTO TRES CA',  'J-92000003-0'),
]

# Mismas cabeceras que ya usa el módulo para exportar (wizard_libro_ventas.py
# / wizard_libro_compras.py, ambos con acción "Exportar Excel") — columna por
# columna, para que el CSV sintético sea representativo del formato real
# (antes traía solo un subconjunto reducido).
_HEADERS_VENTAS = [
    'Op. N°', 'Fecha', 'RIF', 'Nombre o Razón Social',
    'N° Comprobante', 'N° Factura', 'N° Control',
    'N° Nota Débito', 'N° Nota Crédito', 'Tipo', 'N° Fact. Afectada',
    'Total c/IVA', 'V. Internas No Gravadas',
    'Base 16%', 'IVA 16%', 'Base 8%', 'IVA 8%',
    'IVA Retenido', 'IVA Percibido',
]
_CLAVES_VENTAS = [
    'op_nro', 'fecha', 'rif', 'nombre',
    'nro_comprobante', 'nro_factura', 'nro_control',
    'nro_nd', 'nro_nc', 'tipo', 'nro_fact_afectada',
    'total_civa', 'no_gravadas',
    'base_16', 'iva_16', 'base_8', 'iva_8',
    'iva_retenido', 'iva_percibido',
]
_HEADERS_COMPRAS = [
    'Op. N°', 'Fecha Factura', 'RIF', 'Nombre/Razón Social',
    'N° Comprobante (Proveedor)', 'N° de Control',
    'N° Nota Débito', 'N° Nota Crédito', 'Tipo Transcc.', 'N° Fact. Afectada',
    'Total Compras Incl. IVA', 'Compras sin Crédito IVA',
    'Base Imponible 16%', 'IVA 16%', 'Base Imponible 8%', 'IVA 8%',
    'IVA Retenido al Vendedor', 'N° Comp. Retención',
]
_CLAVES_COMPRAS = [
    'op_nro', 'fecha', 'rif', 'nombre',
    'nro_comprobante', 'nro_control',
    'nro_nd', 'nro_nc', 'tipo', 'nro_fact_afectada',
    'total_civa', 'sin_credito',
    'base_16', 'iva_16', 'base_8', 'iva_8',
    'iva_retenido', 'nro_comp_retencion',
]


def _nro_comprobante_sintetico(i):
    """N° de comprobante de 14 dígitos, consistente entre el Libro de Ventas
    sintético, el ve.wh.iva y el PDF de prueba del mismo cliente (posición
    `i`, 1-indexado)."""
    return f'{10000000000000 + i:014d}'


class VeResetPilotoWizard(models.TransientModel):
    _name = 've.reset.piloto.wizard'
    _description = 'Reiniciar Período Demo — Piloto SmartIVA Conecta'

    resultado = fields.Text(string='Resultado', readonly=True)
    ejecutado = fields.Boolean(default=False)

    libro_ventas_csv = fields.Binary(string='Libro de Ventas (CSV)', readonly=True)
    libro_ventas_filename = fields.Char(readonly=True)
    libro_compras_csv = fields.Binary(string='Libro de Compras (CSV)', readonly=True)
    libro_compras_filename = fields.Char(readonly=True)
    comprobantes_pdf_html = fields.Html(
        string='Comprobantes PDF de Prueba (para el Buzón)',
        compute='_compute_comprobantes_pdf_html', sanitize=False)

    def _compute_comprobantes_pdf_html(self):
        for rec in self:
            atts = self.env['ir.attachment'].search([
                ('res_model', '=', self._name), ('res_id', '=', rec.id),
                ('name', 'like', 'comprobante_piloto_%'),
            ], order='name')
            if not atts:
                rec.comprobantes_pdf_html = False
                continue
            rec.comprobantes_pdf_html = ''.join(
                f'<div style="margin-bottom:4px;">'
                f'<a href="/web/content/{a.id}?download=true" target="_blank" '
                f'class="btn btn-sm btn-outline-primary">'
                f'<i class="fa fa-download"/>&#160;{a.name}</a></div>'
                for a in atts
            )

    # ── Acción principal ────────────────────────────────────────────────────

    def action_generar(self):
        self.ensure_one()
        log = []
        company = self._asegurar_compania_piloto(log)
        year, month, quincena, fecha_ini, fecha_fin = self._periodo_activo_hoy()
        periodo_r = f'{year:04d}-{month:02d} {quincena}'

        self._reiniciar_periodo_piloto(company, periodo_r, log)

        periodo_rec = self._asegurar_periodo(company, year, month, quincena,
                                              fecha_ini, fecha_fin, log)
        cuentas = self._asegurar_contabilidad_basica(company, log)

        clientes = self._asegurar_partners(
            company, _PILOTO_CLIENTES, es_spe=True, log=log,
            receivable_acct=cuentas['receivable'])
        proveedores = self._asegurar_partners(
            company, _PILOTO_PROVEEDORES, es_spe=False, log=log,
            payable_acct=cuentas['payable'])

        filas_ventas = self._filas_libro_ventas(clientes, year, month, quincena)
        filas_compras = self._filas_libro_compras(proveedores, year, month, quincena)

        self.libro_ventas_csv = base64.b64encode(
            self._csv_bytes(_HEADERS_VENTAS, _CLAVES_VENTAS, filas_ventas))
        self.libro_ventas_filename = f'libro_ventas_piloto_{year}{month:02d}_{quincena}.csv'
        self.libro_compras_csv = base64.b64encode(
            self._csv_bytes(_HEADERS_COMPRAS, _CLAVES_COMPRAS, filas_compras))
        self.libro_compras_filename = f'libro_compras_piloto_{year}{month:02d}_{quincena}.csv'

        n_ventas = self._crear_facturas_venta(company, clientes, filas_ventas, log)
        self._variar_escenarios_venta(company, filas_ventas, log)
        self._vincular_periodo_wh_iva(company, periodo_rec, log)
        self._marcar_pagos_venta(company, filas_ventas, log)
        n_compras = self._crear_facturas_compra(company, proveedores, filas_compras, log)
        n_prov = self._crear_retenciones_prov(company, proveedores, filas_compras, periodo_rec, log)
        n_seniat = self._crear_seniat_retenciones(company, periodo_rec, filas_ventas, log)

        n_pdf = self._generar_comprobantes_pdf(company, clientes, filas_ventas, log)
        self._regenerar_refs_placeholder(company, log)
        self._corregir_prov_declarado(company, log)

        log.append('')
        log.append(
            f'Listo. Compañía: {company.name} — Período {periodo_rec.periodo_retencion}')
        log.append(
            f'{n_ventas} factura(s) de venta / {n_compras} factura(s) de compra / '
            f'{n_prov} retención(es) IVA Proveedores / {n_seniat} retención(es) SENIAT / '
            f'{n_pdf} comprobante(s) PDF de prueba (adjuntos de este wizard).')
        log.append(
            'Cambie a esta compañía (selector arriba a la derecha) para ver el '
            'Dashboard, la Lista de Trabajo y el Buzón de Comprobantes con esta data.')

        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name,
            'res_id': self.id, 'view_mode': 'form', 'target': 'new',
        }

    # ── Reinicio del período (decisión 2026-07-21: este wizard ya no es un
    #    generador "seguro de re-correr" — cada corrida debe dar un dataset
    #    limpio y predecible, igual que "Reiniciar Demo" del demo general.
    #    Antes dejaba Conciliaciones/Declaraciones de corridas anteriores a
    #    medio camino, lo que además causaba que otros pasos del wizard
    #    pisaran en silencio progreso real hecho a mano.) ─────────────────

    def _reiniciar_periodo_piloto(self, company, periodo_r, log):
        """Borra conciliación, declaración, retenciones (clientes y
        proveedores), SENIAT y facturas/pagos de la compañía piloto para
        este período — antes de regenerar todo desde cero."""
        Periodo = self.env['ve.conciliacion.periodo'].sudo()
        WhIva = self.env['ve.wh.iva'].sudo()
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Seniat = self.env['ve.seniat.retencion'].sudo()
        Payment = self.env['account.payment'].sudo()
        Move = self.env['account.move'].sudo()

        # Pagos primero — un pago conciliado contra una factura bloquea
        # poder pasarla a borrador para poder eliminarla.
        pagos = Payment.search([('company_id', '=', company.id)])
        n_pagos = len(pagos)
        for pago in pagos:
            try:
                if pago.state == 'paid' and hasattr(pago, 'action_draft'):
                    pago.action_draft()
                pago.unlink()
            except Exception as exc:
                log.append(f'  ↩ Pago {pago.name or pago.id} no eliminado: {exc}')
        if n_pagos:
            log.append(f'✗ {n_pagos} pago(s) de prueba anteriores eliminados')

        periodo = Periodo.search([
            ('company_id', '=', company.id), ('periodo_retencion', '=', periodo_r),
        ], limit=1)
        if periodo:
            n_wh = WhIva.search_count([('conciliacion_id', '=', periodo.id)])
            WhIva.search([('conciliacion_id', '=', periodo.id)]).unlink()
            if periodo.declaracion_iva_id:
                WhIvaProv.search(
                    [('declaracion_iva_id', '=', periodo.declaracion_iva_id.id)]).unlink()
                periodo.declaracion_iva_id.unlink()
            Seniat.search([('conciliacion_id', '=', periodo.id)]).unlink()
            periodo.unlink()
            log.append(
                f'✗ Período demo anterior "{periodo_r}" (id={periodo.id}, '
                f'{n_wh} retención(es)) eliminado — conciliación, declaración, '
                f'retenciones, SENIAT')
        else:
            # Diagnóstico: si esto aparece y se esperaba un período previo,
            # es que periodo_r no coincidió con lo guardado — revisar el
            # valor exacto contra ve.conciliacion.periodo.periodo_retencion.
            log.append(f'= No había período anterior "{periodo_r}" que borrar en esta compañía')
        # Huérfanas de esta compañía sin período (ej. arrastre de un
        # período ya eliminado en una corrida previa).
        huerfanas = WhIva.search([
            ('company_id', '=', company.id), ('conciliacion_id', '=', False),
        ])
        if huerfanas:
            huerfanas.unlink()

        # ve.wh.iva.prov (retenciones IVA Proveedores) de OTROS períodos de
        # esta compañía (no solo el que se está regenerando) — el borrado
        # de arriba solo alcanza las vinculadas a la declaración del
        # período `periodo_r` puntual. Cualquier retención de proveedores
        # de una corrida/período anterior distinto queda suelta y bloquea
        # por FK (invoice_id) el borrado de las facturas de compra de
        # abajo. Bug real reportado 2026-07-28: "no borró ni facturas ni
        # retenciones a proveedores" (mismo fix que _borrar_todo_piloto).
        retenciones_prov = WhIvaProv.search([('company_id', '=', company.id)])
        if retenciones_prov:
            n_prov = len(retenciones_prov)
            retenciones_prov.unlink()
            log.append(f'✗ {n_prov} retención(es) IVA Proveedores de corridas anteriores eliminadas')

        # Sin restringir move_type: notas de crédito/débito (out_refund/
        # in_refund) y asientos varios (entry) que el módulo también genera
        # en otros flujos quedaban sin borrar si se filtraba solo a
        # out_invoice/in_invoice — y esos bloqueaban igual el borrado del
        # contacto ("se usa en Contabilidad"). Bug real 2026-07-28.
        facturas = Move.search([('company_id', '=', company.id)])
        n_fact = len(facturas)
        for inv in facturas:
            try:
                with self.env.cr.savepoint():
                    if inv.state == 'posted':
                        inv.button_draft()
                    inv.unlink()
            except Exception as exc:
                log.append(f'  ↩ Factura {inv.name or inv.id} no eliminada: {exc}')
        if n_fact:
            log.append(f'✗ {n_fact} factura(s) de prueba anteriores eliminadas')

    # ── Borrar TODO (sin regenerar) — pedido explícito 2026-07-24: opción
    #    para dejar la compañía piloto sin ninguna data, para probar
    #    CONECTA-14 (el wizard de carga real) desde cero, sin la data
    #    sintética de _reiniciar_periodo_piloto de por medio. A diferencia
    #    de ese método (que solo limpia el período ACTIVO antes de
    #    regenerarlo), esto borra TODOS los períodos/facturas de la
    #    compañía, sin excepción, y no crea nada después. ─────────────────

    def action_borrar_todo(self):
        self.ensure_one()
        log = []
        Company = self.env['res.company'].sudo()
        company = Company.search([('vat', '=', _PILOTO_COMPANY_VAT)], limit=1)
        if not company:
            log.append('= No existe la compañía piloto — nada que borrar.')
            self.resultado = '\n'.join(log)
            self.ejecutado = True
            return {
                'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new',
            }
        self._borrar_todo_piloto(company, log)
        log.append('')
        log.append(f'Listo. Compañía "{company.name}" sin datos: retenciones, facturas, '
                   'conciliaciones, clientes y proveedores.')
        self.resultado = '\n'.join(log)
        self.ejecutado = True
        self.libro_ventas_csv = False
        self.libro_compras_csv = False
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name,
            'res_id': self.id, 'view_mode': 'form', 'target': 'new',
        }

    def _borrar_todo_piloto(self, company, log):
        """Borra TODA la data transaccional de la compañía piloto — todos
        los períodos (no solo el activo), todas las facturas, todas las
        cargas CONECTA-14, y los clientes/proveedores (res.partner) creados
        para esta compañía. No toca la compañía en sí ni su configuración
        contable básica (cuentas/diarios) — solo los datos, para poder
        volver a cargar desde cero limpio. Pedido explícito 2026-07-24:
        antes NO borraba partners, pero eso impedía limpiar clientes que
        habían quedado mal armados (ver company_id fix del mismo día)."""
        Periodo = self.env['ve.conciliacion.periodo'].sudo()
        WhIva = self.env['ve.wh.iva'].sudo()
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Seniat = self.env['ve.seniat.retencion'].sudo()
        Declaracion = self.env['ve.declaracion.iva'].sudo()
        Payment = self.env['account.payment'].sudo()
        Move = self.env['account.move'].sudo()
        CargaVentas = self.env['ve.conecta.carga.ventas'].sudo()
        CargaCompras = self.env['ve.conecta.carga.compras'].sudo()
        Partner = self.env['res.partner'].sudo()

        # Wizards transitorios de "Pagar" que quedaron vivos bloquean el
        # borrado de pagos/clientes por su FK partner_id (restrict) — bug
        # reportado 2026-07-24: CONECTA-14 los creaba y nunca los borraba
        # (ya corregido ahí, esto es limpieza de lo que quedó de antes).
        PaymentRegister = self.env['account.payment.register'].sudo()
        colgados = PaymentRegister.search([])
        if colgados:
            n = len(colgados)
            colgados.unlink()
            log.append(f'✗ {n} wizard(s) de "Pagar" colgados eliminados (bloqueaban el borrado)')

        # Bug reportado 2026-07-24: sin savepoint por registro, un solo
        # fallo de PostgreSQL (ej. FK violation) deja la transacción
        # "envenenada" — hasta el propio mensaje de log para el error
        # siguiente fallaba (InFailedSqlTransaction). Cada unlink() de
        # aquí en adelante va en su propio savepoint, para que un fallo
        # puntual no tumbe el resto del borrado.
        pagos = Payment.search([('company_id', '=', company.id)])
        n_pagos = n_pagos_err = 0
        for pago in pagos:
            try:
                with self.env.cr.savepoint():
                    if pago.state == 'paid' and hasattr(pago, 'action_draft'):
                        pago.action_draft()
                    pago.unlink()
                n_pagos += 1
            except Exception as exc:
                n_pagos_err += 1
                log.append(f'  ↩ Pago {pago.id} no eliminado: {exc}')
        if n_pagos:
            log.append(f'✗ {n_pagos} pago(s) eliminados')

        periodos = Periodo.search([('company_id', '=', company.id)])
        if periodos:
            n_wh = WhIva.search_count([('conciliacion_id', 'in', periodos.ids)])
            try:
                with self.env.cr.savepoint():
                    WhIva.search([('conciliacion_id', 'in', periodos.ids)]).unlink()
                    Seniat.search([('conciliacion_id', 'in', periodos.ids)]).unlink()
                    declaraciones = Declaracion.search([('conciliacion_id', 'in', periodos.ids)])
                    if declaraciones:
                        WhIvaProv.search([('declaracion_iva_id', 'in', declaraciones.ids)]).unlink()
                        declaraciones.unlink()
                    n_periodos = len(periodos)
                    periodos.unlink()
                log.append(
                    f'✗ {n_periodos} período(s) eliminados ({n_wh} retención(es) IVA '
                    f'Clientes, sus declaraciones y retenciones SENIAT/Proveedores)')
            except Exception as exc:
                log.append(f'  ↩ Períodos/retenciones no eliminados: {exc}')
        else:
            log.append('= No había períodos que borrar')

        # Huérfanas de esta compañía sin período (arrastre de corridas previas).
        huerfanas = WhIva.search([
            ('company_id', '=', company.id), ('conciliacion_id', '=', False),
        ])
        if huerfanas:
            try:
                with self.env.cr.savepoint():
                    n = len(huerfanas)
                    huerfanas.unlink()
                log.append(f'✗ {n} retención(es) huérfana(s) eliminadas')
            except Exception as exc:
                log.append(f'  ↩ Retenciones huérfanas no eliminadas: {exc}')

        # ve.wh.iva.prov (retenciones IVA Proveedores) — el borrado de
        # arriba (dentro del bloque de "periodos") solo alcanza las
        # vinculadas a la declaración de un período. Cualquier retención de
        # proveedores suelta (sin ese vínculo, el caso normal de CONECTA-14
        # Compras si no se corrió "Conciliar"/"Declarar") queda sin borrar
        # y bloquea por FK (invoice_id) el unlink() de las facturas de
        # compra de abajo. CORRECCIÓN 2026-07-28 (segunda vuelta): este
        # bloque se había agregado por error solo en
        # _reiniciar_periodo_piloto, faltaba acá en _borrar_todo_piloto —
        # mismo bug reportado de nuevo: "no borró ni facturas ni
        # retenciones a proveedores".
        retenciones_prov = WhIvaProv.search([('company_id', '=', company.id)])
        if retenciones_prov:
            try:
                with self.env.cr.savepoint():
                    n = len(retenciones_prov)
                    retenciones_prov.unlink()
                log.append(f'✗ {n} retención(es) IVA Proveedores eliminadas')
            except Exception as exc:
                log.append(f'  ↩ Retenciones IVA Proveedores no eliminadas: {exc}')

        # Sin restringir move_type: notas de crédito/débito (out_refund/
        # in_refund) y asientos varios (entry) que el módulo también genera
        # en otros flujos quedaban sin borrar si se filtraba solo a
        # out_invoice/in_invoice — y esos bloqueaban igual el borrado del
        # contacto ("se usa en Contabilidad"). Bug real 2026-07-28.
        facturas = Move.search([('company_id', '=', company.id)])
        n_fact = n_fact_err = 0
        for inv in facturas:
            try:
                with self.env.cr.savepoint():
                    if inv.state == 'posted':
                        inv.button_draft()
                    inv.unlink()
                n_fact += 1
            except Exception as exc:
                n_fact_err += 1
                log.append(f'  ↩ Factura {inv.name or inv.id} no eliminada: {exc}')
        if n_fact:
            log.append(f'✗ {n_fact} factura(s) eliminadas')

        cargas = CargaVentas.search([('company_id', '=', company.id)])
        if cargas:
            try:
                with self.env.cr.savepoint():
                    n = len(cargas)
                    cargas.unlink()
                log.append(f'✗ {n} carga(s) de Libro de Ventas (CONECTA-14) eliminadas')
            except Exception as exc:
                log.append(f'  ↩ Cargas Libro de Ventas no eliminadas: {exc}')

        cargas_compras = CargaCompras.search([('company_id', '=', company.id)])
        if cargas_compras:
            try:
                with self.env.cr.savepoint():
                    n = len(cargas_compras)
                    cargas_compras.unlink()
                log.append(f'✗ {n} carga(s) de Libro de Compras (CONECTA-14) eliminadas')
            except Exception as exc:
                log.append(f'  ↩ Cargas Libro de Compras no eliminadas: {exc}')

        # Clientes/proveedores creados para esta compañía — nunca el propio
        # contacto de la compañía (company.partner_id).
        partners = Partner.search([
            ('company_id', '=', company.id),
            ('id', '!=', company.partner_id.id),
        ])
        n_part = n_part_err = n_part_arch = 0
        for p in partners:
            try:
                with self.env.cr.savepoint():
                    p.unlink()
                n_part += 1
            except Exception as exc:
                # Caso real encontrado 2026-07-28: Odoo rechaza el unlink
                # ("se usa en Contabilidad") por un cálculo interno de
                # saldo (debit/credit, no-stored) que no coincide con lo
                # que cualquier búsqueda externa de account.move.line
                # encuentra — causa no identificada, pero el archivado
                # (active=False) sí funciona y saca al contacto de en
                # medio (no vuelve a matchear por RIF en cargas futuras,
                # ver _norm_rif en ve_conecta_carga_*.py).
                try:
                    with self.env.cr.savepoint():
                        p.write({'active': False})
                    n_part_arch += 1
                    log.append(f'  ↺ Cliente/proveedor id={p.id} no se pudo borrar, archivado en su lugar: {exc}')
                except Exception as exc2:
                    n_part_err += 1
                    log.append(f'  ↩ Cliente/proveedor id={p.id} no eliminado ni archivado: {exc2}')
        if n_part:
            log.append(f'✗ {n_part} cliente(s)/proveedor(es) eliminados')
        if n_part_arch:
            log.append(f'↺ {n_part_arch} cliente(s)/proveedor(es) archivados (no se pudieron borrar)')
        if n_pagos_err or n_fact_err or n_part_err:
            log.append(
                f'⚠ {n_pagos_err} pago(s) / {n_fact_err} factura(s) / {n_part_err} '
                'cliente(s) NO se pudieron eliminar (detalle arriba) — probablemente '
                'quedan reconciliados o referenciados por otro registro.')

    # ── Compañía y período ──────────────────────────────────────────────────

    def _asegurar_compania_piloto(self, log):
        Company = self.env['res.company'].sudo()
        country_ve = self.env['res.country'].sudo().search([('code', '=', 'VE')], limit=1)
        company = Company.search([('vat', '=', _PILOTO_COMPANY_VAT)], limit=1)
        if company:
            log.append(f'= Compañía {company.name} (ya existía)')
            if not company.country_id and country_ve:
                company.write({'country_id': country_ve.id})
                log.append('  ↩ País (Venezuela) completado en la compañía existente')
            return company

        # La búsqueda por VAT no ve compañías ARCHIVADAS (active=False,
        # default de search()) — si una recreación anterior del piloto
        # archivó la compañía vieja pero sin limpiarle el `name` (patrón
        # documentado: se le vacía el `vat` para liberar el RIF, pero el
        # nombre "PILOTO SMARTIVA CONECTA CA" puede quedar igual), el
        # create() de abajo choca con la restricción de nombre único de
        # res.company y sale "El nombre de la empresa debe ser único" — sin
        # que se vea ninguna compañía activa con ese nombre en el selector.
        # Se reactiva en vez de crear una duplicada.
        archivada = Company.with_context(active_test=False).search([
            ('name', '=', _PILOTO_COMPANY_NAME), ('active', '=', False),
        ], limit=1)
        if archivada:
            vals_reactivar = {'active': True, 'vat': _PILOTO_COMPANY_VAT}
            if country_ve and not archivada.country_id:
                vals_reactivar['country_id'] = country_ve.id
            archivada.write(vals_reactivar)
            log.append(
                f'↺ Compañía {archivada.name} (id={archivada.id}) estaba '
                'archivada de una recreación anterior — reactivada y RIF '
                'restaurado en vez de crear una duplicada.'
            )
            return archivada

        vals = {
            'name': _PILOTO_COMPANY_NAME,
            'vat': _PILOTO_COMPANY_VAT,
        }
        if country_ve:
            vals['country_id'] = country_ve.id
        # Detectado 2026-07-20: crear una compañía nueva en esta base (incluso
        # manualmente desde Ajustes > Compañías) dispara una inconsistencia
        # cross-company porque "Carpeta de Contabilidad" (account_folder_id,
        # integración Documents-Contabilidad) queda apuntando a la carpeta
        # "Finanzas" de OTRA compañía en vez de crear/asignar una propia.
        # Se fuerza explícito a False para no heredar ese default — best
        # effort, no verificado en vivo; si algún otro módulo la reasigna
        # después de create(), esto no alcanza y hay que revisar la carpeta
        # "Finanzas" del app Documents directamente.
        if 'account_folder_id' in self.env['res.company']._fields:
            vals['account_folder_id'] = False

        try:
            company = Company.create(vals)
        except Exception as exc:
            # Ya se descartó el caso "archivada con el mismo nombre" arriba
            # — si igual falla por nombre único, es una compañía ACTIVA con
            # ese nombre y otro RIF (duplicado real, no detectable por VAT).
            # IMPORTANTE: un `raise` a secas relanza la excepción ORIGINAL
            # de Odoo (el popup genérico "El nombre de la empresa debe ser
            # único") y el mensaje accionable de abajo nunca llega a
            # mostrarse — por eso se envuelve en UserError explícito.
            if 'nombre de la empresa debe ser' in str(exc).lower() or 'unique' in str(exc).lower():
                raise UserError(
                    f'Ya existe una compañía ACTIVA llamada "{_PILOTO_COMPANY_NAME}" '
                    f'con un RIF distinto a {_PILOTO_COMPANY_VAT} — revisar en '
                    'Ajustes > Empresas cuál es y decidir si hay que renombrarla o '
                    'archivarla antes de reintentar.\n\nError original: '
                    f'{exc}'
                ) from exc
            raise UserError(
                f'No se pudo crear la compañía piloto: {exc}\n'
                'El mismo error ocurre creando la compañía manualmente desde '
                'Ajustes > Compañías — no es un bug de este wizard. Revisar la '
                'carpeta "Finanzas" del app Documents (Ajustes > Documentos) '
                'antes de reintentar.'
            ) from exc
        log.append(f'+ Compañía {company.name} creada')
        # Sin esto el usuario actual no puede operar en la compañía nueva
        # ni cambiarse a ella desde el selector.
        self.env.user.sudo().write({'company_ids': [(4, company.id)]})
        return company

    def _periodo_activo_hoy(self):
        hoy = _date.today()
        if hoy.day <= 15:
            year, month, quincena = hoy.year, hoy.month, '1Q'
            fecha_ini = f'{year:04d}-{month:02d}-01'
            fecha_fin = f'{year:04d}-{month:02d}-15'
        else:
            year, month, quincena = hoy.year, hoy.month, '2Q'
            ld = calendar.monthrange(year, month)[1]
            fecha_ini = f'{year:04d}-{month:02d}-16'
            fecha_fin = f'{year:04d}-{month:02d}-{ld:02d}'
        return year, month, quincena, fecha_ini, fecha_fin

    def _asegurar_periodo(self, company, year, month, quincena, fecha_ini, fecha_fin, log):
        periodo_f = f'{year:04d}-{month:02d}'
        periodo_r = f'{periodo_f} {quincena}'
        Periodo = self.env['ve.conciliacion.periodo'].sudo()
        periodo = Periodo.search([
            ('periodo_retencion', '=', periodo_r),
            ('company_id', '=', company.id),
        ], limit=1)
        if periodo:
            log.append(f'= Período {periodo_r} de {company.name} (ya existía)')
            return periodo
        periodo = Periodo.create({
            'periodo': periodo_f,
            'periodo_retencion': periodo_r,
            'fecha_inicio': fecha_ini,
            'fecha_fin': fecha_fin,
            'company_id': company.id,
        })
        log.append(f'+ Período {periodo_r} creado para {company.name}')
        return periodo

    def _asegurar_partners(self, company, definiciones, es_spe, log,
                            receivable_acct=None, payable_acct=None):
        # with_company(company): property_account_receivable_id/payable_id
        # son company_dependent — sin fijar la compañía activa aquí, Odoo
        # graba el valor bajo self.env.company (la compañía activa de la
        # sesión, normalmente DJCS), no bajo PILOTO. Eso deja el partner con
        # company_id=PILOTO pero su property leída en contexto PILOTO
        # inconsistente con otra compañía → "Operación no válida" al
        # escribir/postear.
        Partner = self.env['res.partner'].sudo().with_company(company)
        result = {}
        for name, vat, *resto in definiciones:
            partner = Partner.search([('vat', '=', vat)], limit=1)
            # company_id explícito: estos clientes/proveedores son propios de
            # la compañía piloto (relación comercial real de ESA empresa, no
            # un tercero compartido entre compañías) — sin esto quedan
            # globales (company_id=False) y aparecen mezclados con los
            # clientes de cualquier otra compañía de la base, incluso al
            # trabajar solo en PILOTO.
            vals = {'name': name, 'vat': vat, 'company_type': 'company',
                    'company_id': company.id}
            if es_spe:
                vals['es_agente_retencion'] = True
                vals['porcentaje_retencion_default'] = resto[0] if resto else 75.0
                vals['customer_rank'] = 1
                if receivable_acct:
                    vals['property_account_receivable_id'] = receivable_acct.id
            else:
                vals['supplier_rank'] = 1
                if payable_acct:
                    vals['property_account_payable_id'] = payable_acct.id
            if partner:
                partner.write(vals)
            else:
                partner = Partner.create(vals)
                log.append(f'+ Partner {name} ({vat})')
            result[vat] = partner
        return result

    # ── Generación de filas (Libro de Ventas / Libro de Compras) ───────────

    def _filas_libro_ventas(self, clientes, year, month, quincena):
        day0 = 16 if quincena == '2Q' else 1
        periodo_fiscal = f'{year:04d}-{month:02d}'
        filas = []
        for i, (nombre, vat, pct) in enumerate(_PILOTO_CLIENTES, start=1):
            fecha = f'{year:04d}-{month:02d}-{day0 + i - 1:02d}'
            # Cliente 3 (Tres): doble alícuota 16%+8%. Cliente 6 (Seis):
            # 8% puro (sin 16%). Cliente 7 (Siete): 100% exento (sin IVA
            # en absoluto) — a pedido de la usuaria, variedad de alícuotas
            # en el Libro de Ventas.
            if i == 6:
                base_16, base_8, no_gravadas = 0.0, 80_000.0, 0.0
            elif i == 7:
                base_16, base_8, no_gravadas = 0.0, 0.0, 90_000.0
            else:
                base_16, base_8 = 100_000.0 * i, (45_000.0 if i == 3 else 0.0)
                no_gravadas = 0.0
            iva_16 = round(base_16 * 0.16, 2)
            iva_8 = round(base_8 * 0.08, 2)
            iva_retenido = round((iva_16 + iva_8) * pct / 100, 2)
            filas.append({
                'op_nro': i, 'fecha': fecha, 'rif': vat, 'nombre': nombre,
                'periodo_fiscal': periodo_fiscal,
                'nro_comprobante': _nro_comprobante_sintetico(i),
                'nro_factura': f'PILV-{year}{month:02d}-{i:04d}',
                'nro_control': f'00-{9000000 + i:07d}',
                'nro_nd': '', 'nro_nc': '', 'tipo': '01', 'nro_fact_afectada': '',
                'base_16': base_16, 'iva_16': iva_16,
                'base_8': base_8, 'iva_8': iva_8,
                'total_civa': round(base_16 + iva_16 + base_8 + iva_8 + no_gravadas, 2),
                'no_gravadas': no_gravadas,
                'iva_retenido': iva_retenido, 'iva_percibido': 0.0,
                'pct': pct,
            })
        return filas

    def _filas_libro_compras(self, proveedores, year, month, quincena):
        day0 = 16 if quincena == '2Q' else 1
        periodo_fiscal = f'{year:04d}-{month:02d}'
        filas = []
        for i, (nombre, vat) in enumerate(_PILOTO_PROVEEDORES, start=1):
            fecha = f'{year:04d}-{month:02d}-{day0 + i:02d}'
            base_16 = 60_000.0 * i
            iva_16 = round(base_16 * 0.16, 2)
            filas.append({
                'op_nro': i, 'fecha': fecha, 'rif': vat, 'nombre': nombre,
                'periodo_fiscal': periodo_fiscal,
                'nro_comprobante': '',  # lo asigna _crear_retenciones_prov al numerar
                'nro_factura': f'PILC-{year}{month:02d}-{i:04d}',
                'nro_control': f'NC-{8000000 + i:07d}',
                'nro_nd': '', 'nro_nc': '', 'tipo': '01', 'nro_fact_afectada': '',
                'base_16': base_16, 'iva_16': iva_16,
                'base_8': 0.0, 'iva_8': 0.0,
                'total_civa': round(base_16 + iva_16, 2), 'sin_credito': 0.0,
                'iva_retenido': round(iva_16 * 0.75, 2), 'nro_comp_retencion': '',
            })
        return filas

    @staticmethod
    def _csv_bytes(headers, claves, filas):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for f in filas:
            writer.writerow([f.get(k, '') for k in claves])
        return buf.getvalue().encode('utf-8-sig')

    # ── Contabilidad mínima (sin depender de instalar ningún plan de cuentas) ─

    def _asegurar_contabilidad_basica(self, company, log):
        """Crea lo mínimo indispensable para postear las facturas sintéticas
        (1 cuenta de ingreso, 1 de gasto, 1 diario de venta, 1 de compra,
        impuestos 16% venta/compra) sin depender de instalar un plan de
        cuentas (Paquete VE u otro) en la compañía piloto — evita arrastrar
        campos legales/de localización que no aplican a una compañía de
        prueba desechable. Idempotente: si ya existe algo (ej. porque sí se
        instaló un plan de cuentas), lo reutiliza en vez de duplicar."""
        Account = self.env['account.account'].sudo()
        Journal = self.env['account.journal'].sudo()
        Tax = self.env['account.tax'].sudo()

        income = Account.search([
            ('account_type', 'in', ('income', 'income_other')),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not income:
            income = Account.create({
                'name': 'Ingresos — Piloto Conecta',
                'code': 'PIL700001',
                'account_type': 'income',
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta de ingreso creada (contabilidad mínima)')

        expense = Account.search([
            ('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost')),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not expense:
            expense = Account.create({
                'name': 'Gastos — Piloto Conecta',
                'code': 'PIL600001',
                'account_type': 'expense',
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta de gasto creada (contabilidad mínima)')

        # Cuentas por Cobrar/Pagar — sin esto, la línea "payment_term" que
        # Odoo arma automáticamente al postear la factura (el saldo con el
        # cliente/proveedor) queda sin account_id y la factura no se puede
        # postear (visto en logs de QA 2026-07-20: violación del check
        # account_move_line_check_accountable_required_fields).
        receivable = Account.search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not receivable:
            receivable = Account.create({
                'name': 'Cuentas por Cobrar — Piloto Conecta',
                'code': 'PIL120001',
                'account_type': 'asset_receivable',
                'reconcile': True,
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta por Cobrar creada (contabilidad mínima)')

        payable = Account.search([
            ('account_type', '=', 'liability_payable'),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not payable:
            payable = Account.create({
                'name': 'Cuentas por Pagar — Piloto Conecta',
                'code': 'PIL220001',
                'account_type': 'liability_payable',
                'reconcile': True,
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta por Pagar creada (contabilidad mínima)')

        if not Journal.search([('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1):
            Journal.create({
                'name': 'Ventas Piloto', 'type': 'sale', 'code': 'PILV',
                'company_id': company.id,
            })
            log.append('+ Diario de ventas creado (contabilidad mínima)')

        if not Journal.search([('type', '=', 'purchase'), ('company_id', '=', company.id)], limit=1):
            Journal.create({
                'name': 'Compras Piloto', 'type': 'purchase', 'code': 'PILC',
                'company_id': company.id,
            })
            log.append('+ Diario de compras creado (contabilidad mínima)')

        # Diario Miscelánea (tipo General) — lo exige action_confirmar() de
        # ve.wh.iva para postear el asiento "IVA Retenido por Cobrar / IVA
        # por Pagar" (_crear_asiento_contable).
        if not Journal.search([('type', '=', 'general'), ('company_id', '=', company.id)], limit=1):
            Journal.create({
                'name': 'Miscelánea Piloto', 'type': 'general', 'code': 'PILM',
                'company_id': company.id,
            })
            log.append('+ Diario Miscelánea creado (contabilidad mínima)')

        # Diario de Banco — sin esto, account.payment.register (usado por
        # _marcar_pagada_sin_comprobante para el caso "pagado sin
        # comprobante") no tiene dónde postear el pago.
        if not Journal.search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1):
            Journal.create({
                'name': 'Banco Piloto', 'type': 'bank', 'code': 'PILB',
                'company_id': company.id,
            })
            log.append('+ Diario de banco creado (contabilidad mínima)')

        # Cuentas "IVA Retenido por Cobrar" / "IVA por Pagar" — antes eran un
        # solo par de ir.config_parameter GLOBAL para toda la base (ver
        # res_company.py), lo que habría hecho que confirmar una retención
        # del piloto intentara usar las cuentas de OTRA compañía (DJCS) y
        # fallara. Ahora cada compañía tiene su propio par en res.company.
        if not company.ve_cuenta_iva_retenido_cobrar_id:
            cta_cobrar_ret = Account.search([
                ('account_type', '=', 'asset_current'),
                ('company_ids', 'in', [company.id]),
                ('code', '=', 'PIL130001'),
            ], limit=1) or Account.create({
                'name': 'IVA Retenido por Cobrar — Piloto',
                'code': 'PIL130001',
                'account_type': 'asset_current',
                'company_ids': [(6, 0, [company.id])],
            })
            company.ve_cuenta_iva_retenido_cobrar_id = cta_cobrar_ret.id
            log.append('+ Cuenta "IVA Retenido por Cobrar" creada y configurada (contabilidad mínima)')

        if not company.ve_cuenta_iva_por_pagar_id:
            cta_pagar_ret = Account.search([
                ('account_type', '=', 'liability_current'),
                ('company_ids', 'in', [company.id]),
                ('code', '=', 'PIL230001'),
            ], limit=1) or Account.create({
                'name': 'IVA por Pagar — Piloto',
                'code': 'PIL230001',
                'account_type': 'liability_current',
                'company_ids': [(6, 0, [company.id])],
            })
            company.ve_cuenta_iva_por_pagar_id = cta_pagar_ret.id
            log.append('+ Cuenta "IVA por Pagar" creada y configurada (contabilidad mínima)')

        # Cuentas donde se posta el monto del impuesto (las líneas de
        # reparto de account.tax exigen cuenta en esta versión de Odoo).
        tax_venta_acct = Account.search([
            ('account_type', '=', 'liability_current'),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not tax_venta_acct:
            tax_venta_acct = Account.create({
                'name': 'IVA Débito Fiscal — Piloto',
                'code': 'PIL210001',
                'account_type': 'liability_current',
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta IVA Débito Fiscal creada (contabilidad mínima)')

        tax_compra_acct = Account.search([
            ('account_type', '=', 'asset_current'),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not tax_compra_acct:
            tax_compra_acct = Account.create({
                'name': 'IVA Crédito Fiscal — Piloto',
                'code': 'PIL110001',
                'account_type': 'asset_current',
                'company_ids': [(6, 0, [company.id])],
            })
            log.append('+ Cuenta IVA Crédito Fiscal creada (contabilidad mínima)')

        tax_group = self._asegurar_tax_group(company, log)
        cuentas_tax = {'sale': tax_venta_acct, 'purchase': tax_compra_acct}
        for tipo_uso, label in (('sale', 'venta'), ('purchase', 'compra')):
            for alicuota in (16.0, 8.0):
                if not Tax.search([
                    ('type_tax_use', '=', tipo_uso), ('amount', '=', alicuota),
                    ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
                ], limit=1):
                    cuenta_tax = cuentas_tax[tipo_uso]
                    rep_lines = [
                        (0, 0, {'factor_percent': 100, 'repartition_type': 'base'}),
                        (0, 0, {
                            'factor_percent': 100, 'repartition_type': 'tax',
                            'account_id': cuenta_tax.id,
                        }),
                    ]
                    tax_vals = {
                        'name': f'IVA {alicuota:.0f}% {label} (Piloto)',
                        'amount': alicuota, 'amount_type': 'percent',
                        'type_tax_use': tipo_uso, 'company_id': company.id,
                        'tax_group_id': tax_group.id,
                        'invoice_repartition_line_ids': rep_lines,
                        'refund_repartition_line_ids': rep_lines,
                    }
                    if company.country_id:
                        tax_vals['country_id'] = company.country_id.id
                    Tax.create(tax_vals)
                    log.append(f'+ Impuesto IVA {alicuota:.0f}% {label} creado (contabilidad mínima)')

        return {
            'income': income, 'expense': expense,
            'receivable': receivable, 'payable': payable,
        }

    def _asegurar_tax_group(self, company, log):
        """account.tax.group es requerido por account.tax en esta versión de
        Odoo — se reusa cualquiera existente en el sistema antes de crear uno
        nuevo, para no depender de que la compañía piloto tenga su propio
        plan de cuentas."""
        TaxGroup = self.env['account.tax.group'].sudo()
        group = TaxGroup.search([('company_id', '=', company.id)], limit=1)
        if not group:
            group = TaxGroup.search([], limit=1)
        if not group:
            group = TaxGroup.create({
                'name': 'IVA (Piloto)',
                'company_id': company.id,
            })
            log.append('+ Grupo de impuestos creado (contabilidad mínima)')
        return group

    # ── Creación de account.move a partir de las filas ──────────────────────

    def _cuenta_diario(self, company, tipo):
        Journal = self.env['account.journal'].sudo()
        Account = self.env['account.account'].sudo()
        if tipo == 'venta':
            journal = Journal.search(
                [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
            account = Account.search(
                [('account_type', 'in', ('income', 'income_other')),
                 ('company_ids', 'in', [company.id]), ('currency_id', '=', False)], limit=1)
        else:
            journal = Journal.search(
                [('type', '=', 'purchase'), ('company_id', '=', company.id)], limit=1)
            account = Account.search(
                [('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost')),
                 ('company_ids', 'in', [company.id]), ('currency_id', '=', False)], limit=1)
        return journal, account

    def _buscar_tax(self, company, tipo_uso, alicuota):
        if not alicuota:
            return self.env['account.tax']
        return self.env['account.tax'].sudo().search([
            ('type_tax_use', '=', tipo_uso), ('amount', '=', alicuota),
            ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
        ], limit=1)

    def _crear_facturas_venta(self, company, clientes, filas, log):
        Move = self.env['account.move'].sudo()
        journal, account = self._cuenta_diario(company, 'venta')
        if not (journal and account):
            log.append('⚠ Falta diario/cuenta de venta en la compañía piloto — facturas de venta omitidas')
            return 0
        tax_16 = self._buscar_tax(company, 'sale', 16.0)
        tax_8 = self._buscar_tax(company, 'sale', 8.0)
        n = 0
        for f in filas:
            partner = clientes.get(f['rif'])
            if not partner:
                continue
            if Move.search([('nro_control', '=', f['nro_control']),
                             ('move_type', '=', 'out_invoice')], limit=1):
                continue
            lineas = []
            if f['base_16']:
                lv = {'name': 'Servicios 16% — data de prueba piloto',
                      'quantity': 1, 'price_unit': f['base_16'], 'account_id': account.id}
                if tax_16:
                    lv['tax_ids'] = [(6, 0, [tax_16.id])]
                lineas.append((0, 0, lv))
            if f['base_8']:
                lv = {'name': 'Servicios 8% — data de prueba piloto',
                      'quantity': 1, 'price_unit': f['base_8'], 'account_id': account.id}
                if tax_8:
                    lv['tax_ids'] = [(6, 0, [tax_8.id])]
                lineas.append((0, 0, lv))
            if f.get('no_gravadas'):
                # Venta exenta — sin tax_ids a propósito, no genera IVA ni
                # retención real (el hook de retención igual crea el
                # ve.wh.iva por ser cliente SPE, pero con monto 0).
                lineas.append((0, 0, {
                    'name': 'Servicios exentos — data de prueba piloto',
                    'quantity': 1, 'price_unit': f['no_gravadas'], 'account_id': account.id,
                }))
            inv = Move.create({
                'move_type': 'out_invoice', 'partner_id': partner.id,
                'invoice_date': f['fecha'], 'journal_id': journal.id,
                'company_id': company.id, 'currency_id': company.currency_id.id,
                'nro_control': f['nro_control'], 'ref': f['nro_factura'],
                'invoice_line_ids': lineas,
            })
            try:
                with self.env.cr.savepoint():
                    inv.action_post()
                n += 1
            except Exception as exc:
                log.append(f'  ⚠ Factura venta {f["nombre"]}: {exc}')
        log.append(f'+ {n} factura(s) de venta creadas y posteadas (Libro de Ventas)')
        return n

    def _crear_facturas_compra(self, company, proveedores, filas, log):
        Move = self.env['account.move'].sudo()
        journal, account = self._cuenta_diario(company, 'compra')
        if not (journal and account):
            log.append('⚠ Falta diario/cuenta de compra en la compañía piloto — facturas de compra omitidas')
            return 0
        tax_16 = self._buscar_tax(company, 'purchase', 16.0)
        n = 0
        for f in filas:
            partner = proveedores.get(f['rif'])
            if not partner:
                continue
            if Move.search([('nro_control', '=', f['nro_control']),
                             ('move_type', '=', 'in_invoice')], limit=1):
                continue
            line_vals = {
                'name': 'Compras — data de prueba piloto',
                'quantity': 1, 'price_unit': f['base_16'], 'account_id': account.id,
            }
            if tax_16:
                line_vals['tax_ids'] = [(6, 0, [tax_16.id])]
            inv = Move.create({
                'move_type': 'in_invoice', 'partner_id': partner.id,
                'invoice_date': f['fecha'], 'journal_id': journal.id,
                'company_id': company.id, 'currency_id': company.currency_id.id,
                'nro_control': f['nro_control'], 'ref': f['nro_factura'],
                'invoice_line_ids': [(0, 0, line_vals)],
            })
            try:
                with self.env.cr.savepoint():
                    inv.action_post()
                n += 1
            except Exception as exc:
                log.append(f'  ⚠ Factura compra {f["nombre"]}: {exc}')
        log.append(f'+ {n} factura(s) de compra creadas y posteadas (Libro de Compras)')
        return n

    # ── Variedad de escenarios (estados) ────────────────────────────────────

    # (posición en _PILOTO_CLIENTES) → estado. Uno se deja en 'esperado'
    # (default del hook, no aparece aquí). Ninguno queda en
    # 'conciliado'/'declarado' de entrada a propósito — eso lo hace la
    # usuaria en vivo con el botón real "Conciliar SENIAT" (ver
    # _crear_seniat_retenciones), no la data de prueba.
    # Cuatro va en 'esperado' (No Recibido) a pesar de tener la factura
    # pagada — a pedido de la usuaria, caso de riesgo real (Cobranza vs.
    # Comprobante) más fuerte que el de Dos (que además está vencido).
    _ESCENARIOS_VENTA = {
        2: ('vencido',    None),
        3: ('borrador',   'manual'),
        4: ('esperado',   None),
        5: ('confirmado', 'email'),
    }
    # Solo estados con comprobante YA recibido llevan los campos comp_*/
    # canal/N° comprobante — 'esperado' y 'vencido' siguen sin comprobante
    # por definición (antes 'vencido' quedaba con esos campos seteados por
    # error, inconsistente con su propio significado). Desde la Etapa 4 del
    # rediseño de 3 ejes, 'conciliado'/'declarado' ya no son valores válidos
    # de `state` (ver models/ve_wh_iva.py) — se quitaron de esta tupla.
    _ESTADOS_CON_COMPROBANTE = ('borrador', 'confirmado')

    def _vincular_periodo_wh_iva(self, company, periodo_rec, log):
        """Vincula al período activo las retenciones IVA Clientes recién
        creadas por _crear_facturas_venta — el hook real de account_move
        (_ve_crear_retencion_esperada) las deja sin conciliacion_id a
        propósito (igual que en producción real, donde ese vínculo lo
        completa "Conciliar SENIAT"). El demo general (wizard_reset_demo.py)
        SÍ las crea con conciliacion_id ya asignado, así que sin este paso
        el piloto quedaba inconsistente: no aparecían en Conciliación
        SENIAT/Declaración IVA hasta correr Conciliar SENIAT a mano."""
        WhIva = self.env['ve.wh.iva'].sudo()
        huerfanas = WhIva.search([
            ('company_id', '=', company.id),
            ('conciliacion_id', '=', False),
        ])
        if huerfanas:
            huerfanas.write({'conciliacion_id': periodo_rec.id})
            log.append(f'+ {len(huerfanas)} retención(es) IVA Clientes vinculadas al período')

    def _variar_escenarios_venta(self, company, filas_ventas, log):
        """Deja cada ve.wh.iva de venta en un estado distinto del ciclo de
        vida (misma variedad que ya tiene wizard_reset_demo.py), en vez de
        los 5 en 'esperado' recién creados por el hook de account_move.
        Escritura directa (sudo + write), no via action_* — mismo patrón que
        el demo general para data de prueba (evita las validaciones
        pensadas para el flujo manual real del usuario)."""
        WhIva = self.env['ve.wh.iva'].sudo()
        hoy = _date.today()
        cambiados = 0
        for i, fila in enumerate(filas_ventas, start=1):
            escenario = self._ESCENARIOS_VENTA.get(i)
            if not escenario:
                continue
            estado, canal = escenario
            wh = WhIva.search([
                ('nro_control', '=', fila['nro_control']),
                ('company_id', '=', company.id),
            ], limit=1)
            if not wh:
                continue
            if wh.estado_conciliacion != 'pendiente' or wh.estado_declaracion == 'declarado':
                # No pisar progreso real de la usuaria: si ya avanzó el
                # comprobante a mano (Conciliar SENIAT / Aprobar / Declarar),
                # re-correr este wizard NO debe regresarlo al escenario
                # original — eso fue justo lo que pasó la primera vez
                # (un cliente que ya estaba Conciliado/Declarado volvió a
                # 'confirmado' al volver a generar data). Desde la Etapa 3
                # del rediseño de 3 ejes, "ya avanzó" ya no se detecta por
                # `state` (Aprobar no lo mueve más) sino por estos 2 campos.
                continue
            if estado in self._ESTADOS_CON_COMPROBANTE:
                vals = {
                    'name': _nro_comprobante_sintetico(i),
                    'nro_documento': fila['nro_factura'],
                    'canal_recepcion': canal,
                    'fecha': hoy,
                    'comp_base_16': fila['base_16'],
                    'comp_iva_16': fila['iva_16'],
                    'comp_base_8': fila['base_8'],
                    'comp_iva_8': fila['iva_8'],
                    'comp_monto_retenido': round(
                        (fila['iva_16'] + fila['iva_8']) * wh.porcentaje_retencion / 100, 2),
                    # C.66 solo al CONFIRMAR — mismo criterio que
                    # action_confirmar en ve_wh_iva.py; recibido/borrador NO
                    # basta (Cliente Tres, 'borrador', debe quedar sin C.66).
                    'incluir_declaracion': estado != 'borrador',
                }
            else:
                # esperado/vencido: sin comprobante todavía, no se tocan
                # comp_*/name/canal.
                vals = {}
            vals['state'] = estado

            if estado == 'vencido' and wh.invoice_id:
                # fecha_vencimiento_entrega se computa desde
                # invoice_id.invoice_date_due (REQ-08) — sin esto, la
                # factura del piloto (creada con la fecha del período
                # activo, sin backdate) calcula un plazo en el FUTURO,
                # inconsistente con forzar state='vencido' a mano. Sin
                # backdatear, este registro no calificaba ni como "Vencido"
                # ni como "No Recibido" en la barra "Crédito del Período por
                # Estado" (ambos buckets filtran por fecha) — quedaba fuera
                # de las 4 categorías, "perdiendo" 1 comprobante del total.
                wh.invoice_id.sudo().invoice_date_due = hoy - timedelta(days=20)

            wh.write(vals)
            cambiados += 1
        log.append(
            f'+ {cambiados} retención(es) IVA Clientes variadas '
            f'(No Recibido x2 / Vencido / Recibido sin confirmar / Confirmado — '
            f'ninguna Conciliada aún, eso se hace en vivo)')

    def _registrar_pago(self, company, filas_ventas, posicion, log, motivo=''):
        """Registra el pago completo de la factura del cliente en `posicion`
        (1-indexado, según _PILOTO_CLIENTES) vía el wizard estándar de pagos."""
        if posicion > len(filas_ventas):
            return
        fila = filas_ventas[posicion - 1]
        Move = self.env['account.move'].sudo()
        inv = Move.search([('nro_control', '=', fila['nro_control']),
                            ('move_type', '=', 'out_invoice')], limit=1)
        if not inv or inv.payment_state in ('paid', 'in_payment'):
            return
        try:
            with self.env.cr.savepoint():
                wizard = self.env['account.payment.register'].sudo().with_context(
                    active_model='account.move', active_ids=inv.ids,
                ).create({'payment_date': fields.Date.today()})
                wizard._create_payments()
            log.append(
                f'+ Factura {inv.name or fila["nro_factura"]} ({fila["nombre"]}) '
                f'marcada como pagada{motivo}.')
        except Exception as exc:
            log.append(f'  ⚠ No se pudo registrar el pago de {fila["nombre"]}: {exc}')

    def _marcar_pagos_venta(self, company, filas_ventas, log):
        # Dos (Vencido, sin comprobante): riesgo real — cliente pagó, el
        # comprobante de retención todavía no llegó, y ya venció el plazo.
        self._registrar_pago(company, filas_ventas, 2, log,
                              motivo=' SIN comprobante recibido — caso de riesgo real (vencido)')
        # Cuatro (No Recibido, sin comprobante, plazo AÚN no vencido): mismo
        # riesgo que Dos pero más temprano en el ciclo — a pedido de la
        # usuaria, variante del caso "pagado sin comprobante".
        self._registrar_pago(company, filas_ventas, 4, log,
                              motivo=' SIN comprobante recibido — caso de riesgo real (no vencido aún)')
        # Cinco (Confirmado): ya con comprobante recibido y confirmado,
        # además pagada — "Proceso de Pago" con comprobante en regla.
        self._registrar_pago(company, filas_ventas, 5, log,
                              motivo=' — comprobante ya confirmado')

    def _crear_retenciones_prov(self, company, proveedores, filas_compras, periodo_rec, log):
        """Crea ve.wh.iva.prov en 2 estados (Borrador/Enviado), uno por
        proveedor del piloto, vinculados a sus facturas de compra.
        Sin 'Declarado' a propósito (igual que Clientes) — la usuaria
        declara IVA Proveedores en vivo, no viene pre-resuelto en la data."""
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        decl = self.env['ve.declaracion.iva'].sudo()._get_or_create_for_periodo(periodo_rec.id)
        Move = self.env['account.move'].sudo()
        now = fields.Datetime.now()
        estados = ['borrador', 'enviado']
        creados = 0
        for i, fila in enumerate(filas_compras, start=1):
            partner = proveedores.get(fila['rif'])
            if not partner:
                continue
            if WhIvaProv.search([('nro_control', '=', fila['nro_control']),
                                  ('partner_id', '=', partner.id)], limit=1):
                continue
            state = estados[(i - 1) % len(estados)]
            inv = Move.search([('nro_control', '=', fila['nro_control']),
                                ('move_type', '=', 'in_invoice')], limit=1)
            vals = {
                'company_id': company.id,
                'partner_id': partner.id,
                'invoice_id': inv.id if inv else False,
                'nro_control': fila['nro_control'],
                'fecha': fila['fecha'],
                'monto_base_16': fila['base_16'],
                'porcentaje_retencion': 75.0,
                'state': state,
                'declaracion_iva_id': decl.id,
            }
            if state == 'enviado':
                vals['fecha_envio'] = now
                vals['enviado_por_id'] = self.env.user.id
            try:
                with self.env.cr.savepoint():
                    WhIvaProv.create(vals)
                creados += 1
            except Exception as exc:
                log.append(f'  ⚠ Error creando retención IVA Prov. {fila["nombre"]}: {exc}')
        return creados

    def _crear_seniat_retenciones(self, company, periodo_rec, filas_ventas, log):
        """Carga ve.seniat.retencion de prueba para ejercitar el botón
        'Conciliar SENIAT' con los 3 casos reales: match exacto (Cliente
        Cinco, ya Conciliado), diferencia de monto (Cliente Cuatro) y una
        retención huérfana (Solo SENIAT, sin contraparte en Odoo)."""
        Seniat = self.env['ve.seniat.retencion'].sudo()
        creados = 0
        if len(filas_ventas) >= 5:
            f5 = filas_ventas[4]
            if not Seniat.search([('nro_control', '=', f5['nro_control']),
                                   ('rif_agente', '=', f5['rif'])], limit=1):
                Seniat.create({
                    'rif_agente': f5['rif'], 'nombre_agente': f5['nombre'],
                    'periodo': f5['fecha'][:7], 'periodo_retencion': periodo_rec.periodo_retencion,
                    'fecha': f5['fecha'], 'nro_control': f5['nro_control'],
                    'monto_base': f5['base_16'], 'monto_retenido': round(f5['iva_16'] * 0.75, 2),
                    'conciliacion_id': periodo_rec.id, 'estado': 'cargado',
                })
                creados += 1
        if len(filas_ventas) >= 4:
            f4 = filas_ventas[3]
            if not Seniat.search([('nro_control', '=', f4['nro_control']),
                                   ('rif_agente', '=', f4['rif'])], limit=1):
                Seniat.create({
                    'rif_agente': f4['rif'], 'nombre_agente': f4['nombre'],
                    'periodo': f4['fecha'][:7], 'periodo_retencion': periodo_rec.periodo_retencion,
                    'fecha': f4['fecha'], 'nro_control': f4['nro_control'],
                    # Monto distinto a propósito — al conciliar debe salir "Con Diferencia".
                    'monto_base': f4['base_16'], 'monto_retenido': round(f4['iva_16'] * 1.00 * 0.9, 2),
                    'conciliacion_id': periodo_rec.id, 'estado': 'cargado',
                })
                creados += 1
        if not Seniat.search([('nro_control', '=', '00-9099999')], limit=1):
            Seniat.create({
                'rif_agente': 'J-91099999-0', 'nombre_agente': 'AGENTE SOLO SENIAT PILOTO CA',
                'periodo': periodo_rec.periodo, 'periodo_retencion': periodo_rec.periodo_retencion,
                'fecha': periodo_rec.fecha_inicio, 'nro_control': '00-9099999',
                'monto_base': 50_000.0, 'monto_retenido': 6_000.0,
                'conciliacion_id': periodo_rec.id, 'estado': 'cargado',
            })
            creados += 1
        log.append(
            f'+ {creados} retención(es) SENIAT cargadas '
            f'(match exacto / con diferencia / solo SENIAT sin contraparte)')
        return creados

    def _regenerar_refs_placeholder(self, company, log):
        """Corridas del wizard anteriores al fix de secuencias scoped a DJCS
        (migración 19.0.2.9.93) dejaron ref='RET-IVA-C/YYYY/????' o ref=''
        grabado en la base — la secuencia ya corregida no las regenera sola
        (el ref se asigna una sola vez, en create()). Se reasignan ahora que
        next_by_code() ya funciona desde cualquier compañía."""
        seq_next = self.env['ir.sequence'].sudo().next_by_code
        corregidos = 0
        for model_name, seq_code in (('ve.wh.iva', 've.wh.iva'),
                                      ('ve.wh.iva.prov', 've.wh.iva.prov')):
            recs = self.env[model_name].sudo().search([
                '|', '|', ('ref', 'like', '%????'), ('ref', '=', False), ('ref', '=', ''),
                ('company_id', '=', company.id),
            ])
            for rec in recs:
                nuevo_ref = seq_next(seq_code)
                if nuevo_ref:
                    rec.ref = nuevo_ref
                    corregidos += 1
        if corregidos:
            log.append(
                f'+ {corregidos} referencia("????"/vacía) de corridas anteriores '
                f'regenerada(s) con la secuencia ya corregida')

    def _corregir_prov_declarado(self, company, log):
        """Corridas anteriores a que se decidiera no incluir 'Declarado' en
        la data de prueba (ve.wh.iva.prov debe simular solo Borrador/Enviado,
        la usuaria declara en vivo) dejaron proveedores en 'declarado' —
        se bajan a 'enviado' para no pre-resolver el flujo.

        Solo toca registros cuya Declaración IVA (cabecera) NO esté también
        en 'declarado' — si ambos coinciden, lo más probable es que la
        usuaria haya declarado IVA Proveedores de verdad (flujo real,
        ve.wizard.declarar.prov), y este wizard de datos de prueba NO debe
        deshacer ese progreso al volver a correrse."""
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo().search([
            ('company_id', '=', company.id), ('state', '=', 'declarado'),
            ('declaracion_iva_id.estado_prov', '!=', 'declarado'),
        ])
        if WhIvaProv:
            WhIvaProv.write({
                'state': 'enviado',
                'declarado_por_id': False,
                'fecha_declaracion': False,
            })
            log.append(
                f'+ {len(WhIvaProv)} retención(es) IVA Proveedores bajadas de '
                f'Declarado a Enviado (no debían venir pre-declaradas)')

    # ── Comprobantes PDF de prueba (para el Buzón) ──────────────────────────

    def _generar_comprobantes_pdf(self, company, clientes, filas_ventas, log):
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib import colors
        except ImportError:
            log.append('⚠ reportlab no disponible — comprobantes PDF de prueba omitidos')
            return 0

        WhIva = self.env['ve.wh.iva'].sudo()
        hoy = _date.today()

        def _pdf_bytes(f, wh, monto_retenido, titulo_extra=''):
            buf = io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                                     topMargin=2 * cm, bottomMargin=2 * cm)
            styles = getSampleStyleSheet()
            title = ParagraphStyle('t', parent=styles['Normal'], fontSize=11,
                                    fontName='Helvetica-Bold', alignment=TA_CENTER)
            data = [
                ['COMPROBANTE DE RETENCIÓN DE IVA — DATA DE PRUEBA PILOTO', ''],
                ['N° de Comprobante', f['nro_comprobante']],
                ['Período Fiscal', f['periodo_fiscal']],
                ['Fecha de Emisión', str(hoy)],
                ['Agente de Retención', f['nombre']],
                ['RIF Agente', f['rif']],
                # Sujeto Retenido = nuestra propia compañía (a quién le retienen) —
                # es lo que permite identificar a qué compañía corresponde el
                # comprobante cuando un mismo canal (WhatsApp/email) recibe
                # comprobantes de varias compañías cliente de un despacho.
                ['Sujeto Retenido', company.name],
                ['RIF Sujeto Retenido', company.vat or '---'],
                ['N° de Control', f['nro_control']],
                ['N° de Factura', f['nro_factura']],
                ['Base Imponible 16%', f'{f["base_16"]:,.2f}'],
                ['IVA 16%', f'{f["iva_16"]:,.2f}'],
            ]
            if f['base_8']:
                data += [
                    ['Base Imponible 8%', f'{f["base_8"]:,.2f}'],
                    ['IVA 8%', f'{f["iva_8"]:,.2f}'],
                ]
            if f.get('no_gravadas'):
                data += [['Base Exenta', f'{f["no_gravadas"]:,.2f}']]
            data += [
                ['% Retención', f'{wh.porcentaje_retencion}'],
                ['Monto Retenido', f'{monto_retenido:,.2f}'],
            ]
            tbl = Table(data, colWidths=[6 * cm, 9 * cm])
            tbl.setStyle(TableStyle([
                ('SPAN', (0, 0), (1, 0)),
                ('ALIGN', (0, 0), (1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 1), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
            ]))
            doc.build([Paragraph(f'SmartIVA Conecta — Piloto{titulo_extra}', title), Spacer(1, 6), tbl])
            return buf.getvalue()

        n = 0
        # Los 5 clientes: 1 y 2 (No Recibido/Vencido) sirven para probar el
        # match normal del OCR; 3, 4 y 5 (ya con comprobante) sirven para
        # probar la detección de posible duplicado si se suben de nuevo.
        for idx, f in enumerate(filas_ventas):
            wh = WhIva.search([
                ('nro_control', '=', f['nro_control']),
                ('company_id', '=', company.id),
            ], limit=1)
            if not wh:
                continue
            monto_ok = round((f['iva_16'] + f['iva_8']) * wh.porcentaje_retencion / 100, 2)

            pdf = _pdf_bytes(f, wh, monto_ok)
            self.env['ir.attachment'].sudo().create({
                'name': f'comprobante_piloto_{f["nro_control"]}.pdf',
                'datas': base64.b64encode(pdf),
                'res_model': self._name, 'res_id': self.id,
                'mimetype': 'application/pdf',
            })
            n += 1

            # Cliente 1 (Uno, todavía 'esperado'): además del PDF normal, uno
            # con el monto retenido intencionalmente distinto — para probar
            # que el Buzón detecta "Con Diferencias" al subirlo (en vez de
            # subir el normal para ese cliente).
            if idx == 0:
                monto_dif = round(monto_ok * 1.15, 2)
                pdf_dif = _pdf_bytes(f, wh, monto_dif, titulo_extra=' (CON DIFERENCIA)')
                self.env['ir.attachment'].sudo().create({
                    'name': f'comprobante_piloto_{f["nro_control"]}_CON_DIFERENCIA.pdf',
                    'datas': base64.b64encode(pdf_dif),
                    'res_model': self._name, 'res_id': self.id,
                    'mimetype': 'application/pdf',
                })
                n += 1
        if n:
            log.append(
                f'+ {n} comprobante(s) PDF de prueba generados — adjuntos de este wizard, '
                'descárguelos y súbalos manualmente al Buzón de Comprobantes para probar '
                'OCR/matching.')
        return n
