import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# ── Período de demo ──────────────────────────────────────────────────────────
_PERIODO_F = '2026-06'
_PERIODO_R = '2026-06 2Q'

# ── Agentes de retención IVA Clientes (SPE que nos retienen a nosotros) ──────
_DEMO_AGENTES = [
    ('DISTRIBUIDORA CENTRAL CA',         'J-99000001-0', 75.0),
    ('SUMINISTROS INDUSTRIALES CA',      'J-99000002-0', 75.0),
    ('SERVICIOS DIGITALES SA',           'J-99000003-0', 75.0),
    ('FARMA PLUS CA',                    'J-99000004-0', 75.0),
    ('SUPERMERCADOS METRO CA',           'J-99000005-0', 75.0),
    ('BEBIDAS DEL LLANO CA',             'J-99000006-0', 75.0),
]
_DEMO_VATS = [v for _, v, _ in _DEMO_AGENTES]

# ── Proveedores IVA Proveedores (a quienes nosotros retenemos) ───────────────
_DEMO_PROVEEDORES = [
    ('SOLUCIONES TECH CA',         'J-88000001-0', True),
    ('MATERIALES Y SERVICIOS CA',  'J-88000002-0', True),
    ('PAPELERIA EL SOL CA',        'J-88000003-0', True),
]
_DEMO_PROV_VATS = [v for _, v, _ in _DEMO_PROVEEDORES]

# ── Descripción del escenario de demo ────────────────────────────────────────
_DEMO_DESCRIPCION = (
    'IVA Clientes: 6 agentes SPE · 11 retenciones Odoo · 8 retenciones SENIAT\n'
    '  +3 Esperados demo canal: Manual / Email / WhatsApp (ver campo Notas)\n'
    'IVA Proveedores: 3 proveedores · 5 comprobantes (16%/8%/mixto) · estados varios\n'
    'Incluye: doble alícuota 16%+8%, un proveedor sin N° Control (100%)'
)


class VeResetDemoWizard(models.TransientModel):
    _name = 've.reset.demo.wizard'
    _description = 'Reinicializar Data Demo'

    resultado = fields.Text(string='Resultado', readonly=True)
    ejecutado = fields.Boolean(default=False)
    conciliacion_id = fields.Many2one(
        've.conciliacion.periodo',
        string='Período',
        readonly=True,
    )
    periodo_label = fields.Char(compute='_compute_periodo_label')

    @api.depends('conciliacion_id')
    def _compute_periodo_label(self):
        for rec in self:
            rec.periodo_label = (
                rec.conciliacion_id.periodo_retencion
                if rec.conciliacion_id else _PERIODO_R
            )

    def action_reiniciar(self):
        self.ensure_one()
        if self.conciliacion_id:
            return self._reiniciar_periodo_activo()
        return self._reiniciar_demo_completo()

    def action_crear_facturas_prov_demo(self):
        """Crea solo las facturas de compra y comprobantes IVA Proveedores demo,
        sin tocar las retenciones de clientes ni el período existente."""
        self.ensure_one()
        log = []
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Periodo   = self.env['ve.conciliacion.periodo'].sudo()

        # Determinar período activo
        if self.conciliacion_id:
            conc    = self.conciliacion_id
            periodo_f = conc.periodo or _PERIODO_F
            periodo_r = conc.periodo_retencion or _PERIODO_R
        else:
            # Buscar el período de demo más reciente
            conc = Periodo.search([], order='fecha_inicio desc', limit=1)
            if not conc:
                self.resultado = '⚠ No hay ningún período creado. Ejecute primero "Reiniciar Demo".'
                self.ejecutado = True
                return {'type': 'ir.actions.act_window', 'res_model': self._name,
                        'res_id': self.id, 'view_mode': 'form', 'target': 'new'}
            periodo_f = conc.periodo or _PERIODO_F
            periodo_r = conc.periodo_retencion or _PERIODO_R

        try:
            year  = int(periodo_f[:4])
            month = int(periodo_f[5:7])
        except (ValueError, IndexError):
            year, month = 2026, 5

        quincena = '2Q' if periodo_r.endswith('2Q') else '1Q'
        log.append(f'Período: {periodo_r} — solo IVA Proveedores')

        # Limpiar comprobantes prov del período (buscar por declaracion_iva_id)
        decl = conc.declaracion_iva_id
        if decl:
            provs = WhIvaProv.search([('declaracion_iva_id', '=', decl.id)])
        else:
            provs = WhIvaProv.search([('periodo_id', '=', conc.id)])
        if provs:
            provs.unlink()
            log.append(f'✗ {len(provs)} comprobantes proveedores eliminados')

        ppids        = self._asegurar_proveedores(log)
        comp_by_ctrl = self._crear_facturas_compra_demo(ppids, year, month, quincena, log)
        self._crear_retenciones_prov(WhIvaProv, ppids, conc.id, year, month, quincena, log, comp_by_ctrl)

        conc.invalidate_recordset()
        decl_iva = conc.declaracion_iva_id
        n_prov = len(decl_iva.wh_iva_prov_ids) if decl_iva else 0
        log.append(f'→ Período tiene ahora {n_prov} comprobantes IVA Proveedores')

        # Si el período ya estaba declarado, marcar los comprobantes y estado_prov también
        if decl_iva and conc.estado == 'declarado':
            activos = decl_iva.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
            now     = fields.Datetime.now()
            uid     = self.env.user.id
            activos.write({
                'state':             'declarado',
                'fecha_envio':       now,
                'enviado_por_id':    uid,
                'fecha_declaracion': now,
                'declarado_por_id':  uid,
            })
            decl_iva.write({
                'estado_prov':          'declarado',
                'declarado_prov_por_id': uid,
                'fecha_declaracion_prov': now,
                'nro_declaracion_prov': 'DEMO-HIST-PROV',
            })
            log.append(f'✓ {len(activos)} comprobantes IVA Prov. marcados como Declarados (período ya declarado)')

        log.append('Listo.')

        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    # ── Modo 3: histórico de N quincenas declaradas (para Tendencias/KPIs) ──

    def action_generar_historico(self, n_quincenas=5):
        """Genera (o regenera desde cero) las N quincenas históricas
        inmediatamente anteriores al período activo, todas en estado
        Declarado, con facturas reales posteadas (para que campo_39/campo_49
        de la Declaración IVA —compute puro sobre account.move— no queden en
        cero) y numeración de N° Control creciente y coherente por agente
        entre quincenas, sin chocar con los rangos ya usados por el período
        activo. Pensado para dejar la serie de Tendencias IVA / KPIs con
        datos congruentes tras un rebuild que borró el histórico.
        """
        self.ensure_one()
        log = []
        Periodo = self.env['ve.conciliacion.periodo'].sudo()
        Declaracion = self.env['ve.declaracion.iva'].sudo()
        WhIva = self.env['ve.wh.iva'].sudo()
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Seniat = self.env['ve.seniat.retencion'].sudo()
        Move = self.env['account.move'].sudo()
        company = self.env.company

        # ── Determinar la quincena activa (más reciente por fecha_fin) ──────
        activa = Periodo.search([], order='fecha_fin desc', limit=1)
        if activa and activa.periodo:
            year = int(activa.periodo[:4])
            month = int(activa.periodo[5:7])
            quincena = '2Q' if (activa.periodo_retencion or '').endswith('2Q') else '1Q'
        else:
            from datetime import date as _date
            hoy = _date.today()
            year, month = hoy.year, hoy.month
            quincena = '1Q' if hoy.day <= 15 else '2Q'

        # ── Calcular las N quincenas ANTERIORES a la activa (más antigua primero) ──
        secuencia = []
        y, m, q = year, month, quincena
        for _ in range(n_quincenas):
            if q == '2Q':
                q = '1Q'
            else:
                q = '2Q'
                m -= 1
                if m == 0:
                    m, y = 12, y - 1
            secuencia.append((y, m, q))
        secuencia.reverse()  # más antigua → más reciente (justo antes de la activa)

        pids = self._asegurar_partners(log)
        ppids = self._asegurar_proveedores(log)

        journal_sale = self.env['account.journal'].sudo().search(
            [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
        journal_purch = self.env['account.journal'].sudo().search(
            [('type', '=', 'purchase'), ('company_id', '=', company.id)], limit=1)
        tax_16_sale = self.env['account.tax'].sudo().search(
            [('type_tax_use', '=', 'sale'), ('amount', '=', 16.0),
             ('amount_type', '=', 'percent'), ('company_id', '=', company.id)], limit=1)
        tax_16_purch = self.env['account.tax'].sudo().search(
            [('type_tax_use', '=', 'purchase'), ('amount', '=', 16.0),
             ('amount_type', '=', 'percent'), ('company_id', '=', company.id)], limit=1)
        # currency_id=False descarta cuentas que obligan moneda secundaria
        # (asientos con amount_currency requerido) — evita el error "La cuenta
        # seleccionada... obliga a que proporcione una moneda secundaria".
        income_acct = self.env['account.account'].sudo().search(
            [('account_type', 'in', ('income', 'income_other')),
             ('company_ids', 'in', [company.id]), ('currency_id', '=', False)], limit=1)
        exp_acct = self.env['account.account'].sudo().search(
            [('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost')),
             ('company_ids', 'in', [company.id]), ('currency_id', '=', False)], limit=1)
        if not (journal_sale and journal_purch and income_acct and exp_acct):
            log.append('⚠ Faltan diarios/cuentas de venta o compra — histórico omitido')
            self.resultado = '\n'.join(log)
            self.ejecutado = True
            return {'type': 'ir.actions.act_window', 'res_model': self._name,
                    'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

        # Base de N° Control por agente, muy por debajo de los rangos ya usados
        # por el período activo (ej. agente 1 usa 0012341+ en el activo; el
        # histórico se queda en 0012000-0012075 → cronológicamente coherente).
        AGENT_BASE_CTRL = {
            'J-99000001-0': 12000, 'J-99000002-0': 23000, 'J-99000003-0': 34000,
            'J-99000004-0': 45000, 'J-99000005-0': 56000, 'J-99000006-0': 67000,
        }
        AGENT_BASE_MONTO = {
            'J-99000001-0': 220_000, 'J-99000002-0': 180_000, 'J-99000003-0': 300_000,
            'J-99000004-0': 90_000, 'J-99000005-0': 160_000, 'J-99000006-0': 250_000,
        }
        PROV_BASE_CTRL = {
            'J-88000001-0': 16100, 'J-88000002-0': 16200, 'J-88000003-0': 16300,
        }
        # Base y crecimiento más alto que el lado ventas para que Margen C/D
        # (Crédito/Débito) tenga una tendencia real ascendente entre quincenas
        # en vez de quedar plano (crédito y débito crecían casi igual antes).
        PROV_BASE_MONTO = {
            'J-88000001-0': 300_000, 'J-88000002-0': 200_000, 'J-88000003-0': 150_000,
        }
        # Variación no uniforme por agente/proveedor y quincena (encima del
        # crecimiento base de arriba): sin esto, TODOS los agentes/proveedores
        # crecían exactamente al mismo ritmo cada período, lo que produce una
        # curva perfectamente suave/recta en vez de una tendencia realista
        # con subidas y bajadas.
        _JITTER_AGENTE = {
            'J-99000001-0': [1.00, 1.04, 0.98, 1.06, 1.01],
            'J-99000002-0': [1.00, 0.95, 1.03, 1.00, 1.08],
            'J-99000003-0': [1.00, 1.02, 1.05, 0.97, 1.03],
            'J-99000004-0': [1.00, 1.06, 0.99, 1.02, 0.96],
            'J-99000005-0': [1.00, 0.98, 1.01, 1.05, 1.02],
            'J-99000006-0': [1.00, 1.03, 0.96, 1.01, 1.07],
        }
        _JITTER_PROV = {
            'J-88000001-0': [1.00, 1.05, 0.97, 1.03, 1.09],
            'J-88000002-0': [1.00, 0.96, 1.04, 1.00, 1.06],
            'J-88000003-0': [1.00, 1.02, 1.08, 0.95, 1.04],
        }

        n_creados = 0
        for p_idx, (y, m, q) in enumerate(secuencia):
            periodo_f = f'{y:04d}-{m:02d}'
            periodo_r = f'{periodo_f} {q}'
            # La quincena más reciente (justo antes de la activa) se deja
            # SIN declarar a propósito — conciliada y lista, pero pendiente
            # de presentar — para que "Períodos Sin Declarar" tenga un caso
            # real que mostrar en vez de que todo el histórico salga siempre
            # declarado.
            es_ultimo = (p_idx == len(secuencia) - 1)

            # Limpiar por completo si ya existía (evita mezclar el stub mínimo
            # de _asegurar_periodo_hist_declarado con este histórico completo)
            existentes = Periodo.search([('periodo_retencion', '=', periodo_r)])
            if existentes:
                WhIva.search([('conciliacion_id', 'in', existentes.ids)]).unlink()
                WhIvaProv.search([('periodo_id', 'in', existentes.ids)]).unlink()
                Seniat.search([('conciliacion_id', 'in', existentes.ids)]).unlink()
                existentes.mapped('declaracion_iva_id').unlink()
                existentes.unlink()
                log.append(f'✗ Período {periodo_r} existente eliminado para regenerar completo')

            import calendar as _cal
            if q == '1Q':
                fecha_ini, fecha_fin, dia_factura = f'{y:04d}-{m:02d}-01', f'{y:04d}-{m:02d}-15', 8
            else:
                ld = _cal.monthrange(y, m)[1]
                fecha_ini = f'{y:04d}-{m:02d}-16'
                fecha_fin = f'{y:04d}-{m:02d}-{ld:02d}'
                dia_factura = 23

            periodo_rec = Periodo.create({
                'name': periodo_r, 'periodo': periodo_f, 'periodo_retencion': periodo_r,
                'fecha_inicio': fecha_ini, 'fecha_fin': fecha_fin,
                'estado': 'aprobado' if es_ultimo else 'declarado',
            })
            fecha_fact = f'{y:04d}-{m:02d}-{dia_factura:02d}'

            # ── Retenciones IVA Clientes: 1 factura + 1 retención + 1 SENIAT por agente ──
            for nombre, vat, pct in _DEMO_AGENTES:
                partner = pids.get(vat)
                if not partner:
                    continue
                ctrl_num = AGENT_BASE_CTRL[vat] + p_idx * 15
                nro_ctrl = f'00-00{ctrl_num:05d}'
                jitter = _JITTER_AGENTE[vat][p_idx % len(_JITTER_AGENTE[vat])]
                base_16 = round(AGENT_BASE_MONTO[vat] * (1 + 0.015 * p_idx) * jitter, -3)
                iva_16 = round(base_16 * 0.16, 2)
                monto_ret = round(iva_16 * pct / 100, 2)

                # Limpiar borradores huérfanos de intentos anteriores con el mismo N° Control
                Move.search([
                    ('nro_control', '=', nro_ctrl), ('move_type', '=', 'out_invoice'),
                    ('state', '=', 'draft'),
                ]).unlink()

                line_vals = {'name': f'Servicios — {periodo_r}', 'quantity': 1,
                             'price_unit': base_16, 'account_id': income_acct.id}
                if tax_16_sale:
                    line_vals['tax_ids'] = [(6, 0, [tax_16_sale.id])]
                inv = Move.create({
                    'move_type': 'out_invoice', 'partner_id': partner.id,
                    'invoice_date': fecha_fact, 'journal_id': journal_sale.id,
                    'company_id': company.id, 'currency_id': company.currency_id.id,
                    'nro_control': nro_ctrl,
                    'invoice_line_ids': [(0, 0, line_vals)],
                })
                try:
                    inv.action_post()
                except Exception as e:
                    log.append(f'  ⚠ Factura venta {nombre}/{periodo_r}: {e}')
                    continue
                # La confirmación auto-crea una ve.wh.iva "esperado"; se reemplaza
                # por la versión "declarado" con los montos exactos de abajo.
                WhIva.search([('invoice_id', '=', inv.id)]).unlink()

                WhIva.create({
                    'name': f'DEMO-{y}{m:02d}-{vat[-6:-2]}-{p_idx}',
                    'partner_id': partner.id, 'company_id': company.id,
                    'periodo': periodo_f, 'fecha': fecha_fact,
                    'invoice_id': inv.id, 'nro_control': nro_ctrl,
                    'nro_documento': inv.name, 'tipo_documento': '01',
                    'alicuota': 16.0, 'monto_base': base_16, 'monto_iva': iva_16,
                    'porcentaje_retencion': pct, 'canal_recepcion': 'email',
                    'state': 'confirmado',
                    'estado_conciliacion': 'listo_declarar' if es_ultimo else 'declarado',
                    'estado_declaracion': 'no_declarado' if es_ultimo else 'declarado',
                    'conciliacion_id': periodo_rec.id,
                    'incluir_declaracion': True,
                    'comp_base_16': base_16, 'comp_iva_16': iva_16,
                    'comp_monto_retenido': monto_ret,
                })
                Seniat.create({
                    'rif_agente': vat, 'nombre_agente': nombre,
                    'nro_control': nro_ctrl, 'nro_documento': inv.name,
                    'tipo_documento': '01', 'periodo': periodo_f,
                    'periodo_retencion': periodo_r, 'fecha': fecha_fact,
                    'monto_base': base_16, 'monto_retenido': monto_ret,
                    'alicuota': 16.0, 'conciliacion_id': periodo_rec.id,
                    'cargado_por_rpa': True,
                })

            # ── Retenciones IVA Proveedores: 1 factura + 1 comprobante por proveedor ──
            decl = Declaracion._get_or_create_for_periodo(periodo_rec.id)
            for nombre, vat, _tiene_ctrl in _DEMO_PROVEEDORES:
                partner = ppids.get(vat)
                if not partner:
                    continue
                ctrl_num = PROV_BASE_CTRL[vat] + p_idx * 5
                nro_ctrl = f'NC-{ctrl_num}'
                jitter = _JITTER_PROV[vat][p_idx % len(_JITTER_PROV[vat])]
                base_16 = round(PROV_BASE_MONTO[vat] * (1 + 0.09 * p_idx) * jitter, -3)

                # Limpiar borradores huérfanos de intentos anteriores con el mismo N° Control
                Move.search([
                    ('nro_control', '=', nro_ctrl), ('move_type', '=', 'in_invoice'),
                    ('state', '=', 'draft'),
                ]).unlink()

                line_vals = {'name': f'Compras — {periodo_r}', 'quantity': 1,
                             'price_unit': base_16, 'account_id': exp_acct.id}
                if tax_16_purch:
                    line_vals['tax_ids'] = [(6, 0, [tax_16_purch.id])]
                inv_c = Move.create({
                    'move_type': 'in_invoice', 'partner_id': partner.id,
                    'invoice_date': fecha_fact, 'journal_id': journal_purch.id,
                    'company_id': company.id, 'currency_id': company.currency_id.id,
                    'ref': f'FACT-HIST-{y}{m:02d}-{vat[-4:]}',
                    'nro_control': nro_ctrl,
                    'invoice_line_ids': [(0, 0, line_vals)],
                })
                try:
                    inv_c.action_post()
                except Exception as e:
                    log.append(f'  ⚠ Factura compra {nombre}/{periodo_r}: {e}')
                    continue

                prov_vals = {
                    'partner_id': partner.id, 'invoice_id': inv_c.id,
                    'nro_control': nro_ctrl, 'fecha': fecha_fact,
                    'monto_base_16': base_16, 'porcentaje_retencion': 75.0,
                    'state': 'enviado' if es_ultimo else 'declarado',
                    'declaracion_iva_id': decl.id,
                    'fecha_envio': fields.Datetime.now(), 'enviado_por_id': self.env.user.id,
                }
                if not es_ultimo:
                    prov_vals.update({
                        'fecha_declaracion': fields.Datetime.now(),
                        'declarado_por_id': self.env.user.id,
                    })
                WhIvaProv.create(prov_vals)

            # ── Cerrar la declaración — 1 de las N quincenas se declara fuera
            # de plazo (>7 días) a propósito: si TODAS quedan siempre dentro
            # del plazo, Puntualidad Fiscal sale en línea recta al 100% y no
            # demuestra el valor real del KPI (detectar incumplimientos).
            if es_ultimo:
                log.append(f'+ Período histórico {periodo_r} dejado SIN declarar '
                            f'(conciliado, listo para declarar)')
            else:
                from datetime import timedelta as _td, datetime as _dt
                dias_declaracion = 9 if p_idx == 1 else 3
                fecha_decl = _dt.strptime(fecha_fin, '%Y-%m-%d') + _td(days=dias_declaracion)
                decl.write({
                    'estado': 'presentada', 'fecha_declaracion': fecha_decl,
                    'nro_declaracion': f'DEMO-HIST-{periodo_f.replace("-", "")}{q}',
                })
                decl.write({
                    'estado_prov': 'declarado', 'declarado_prov_por_id': self.env.user.id,
                    'fecha_declaracion_prov': fecha_decl,
                    'nro_declaracion_prov': f'DEMO-HIST-PROV-{periodo_f.replace("-", "")}{q}',
                })

            n_creados += 1
            log.append(f'+ Período histórico {periodo_r} generado completo (6 ret. clientes + 3 IVA prov.)')

        log.append(f'\nListo. {n_creados} quincena(s) histórica(s) generadas antes de la activa.')
        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    # ── Modo 1: desde el formulario de Declaración (período ya existe) ──────

    def _reiniciar_periodo_activo(self):
        conc = self.conciliacion_id
        periodo_f = conc.periodo or _PERIODO_F
        periodo_r = conc.periodo_retencion or _PERIODO_R
        try:
            year, month = int(periodo_f[:4]), int(periodo_f[5:7])
        except (ValueError, IndexError):
            year, month = 2026, 5

        log = []
        WhIva    = self.env['ve.wh.iva'].sudo()
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Seniat   = self.env['ve.seniat.retencion'].sudo()

        whs = WhIva.search([('conciliacion_id', '=', conc.id)])
        if whs:
            whs.unlink()
            log.append(f'✗ {len(whs)} retenciones Odoo eliminadas')
        provs = WhIvaProv.search([('periodo_id', '=', conc.id)])
        if provs:
            provs.unlink()
            log.append(f'✗ {len(provs)} comprobantes proveedores eliminados')
        seniats = Seniat.search([('conciliacion_id', '=', conc.id)])
        if seniats:
            seniats.unlink()
            log.append(f'✗ {len(seniats)} retenciones SENIAT eliminadas')

        conc.write({
            'estado': 'borrador',
            'aprobado_por': False,
            'fecha_aprobacion': False,
        })
        if conc.declaracion_iva_id:
            conc.declaracion_iva_id.write({
                'estado': 'borrador',
                'nro_declaracion': False,
                'declarado_por_rpa': False,
                'fecha_declaracion': False,
            })
        log.append(f'↺ Período {periodo_r} reiniciado a Borrador')

        quincena = '2Q' if periodo_r.endswith('2Q') else '1Q'
        pids = self._asegurar_partners(log)
        ppids = self._asegurar_proveedores(log)
        inv_by_ctrl = self._crear_facturas_demo(pids, year, month, quincena, log)
        comp_by_ctrl = self._crear_facturas_compra_demo(ppids, year, month, quincena, log)
        self._crear_retenciones_odoo(WhIva, pids, periodo_f, conc.id, year, month, quincena, log, inv_by_ctrl)
        self._crear_retenciones_seniat(Seniat, periodo_f, periodo_r, conc.id, year, month, quincena, log)
        self._crear_retenciones_prov(WhIvaProv, ppids, conc.id, year, month, quincena, log, comp_by_ctrl)
        self._crear_sanciones_demo(log)

        conc.invalidate_recordset()
        log.append('')
        log.append('Listo. Abra el período y presione "Conciliar".')

        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new',
        }

    # ── Modo 2: desde el menú (recreación completa) ────────────────────────

    def _reiniciar_demo_completo(self):
        import calendar as _cal
        from datetime import date as _date
        log = []
        WhIva     = self.env['ve.wh.iva'].sudo()
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        Seniat    = self.env['ve.seniat.retencion'].sudo()
        Periodo   = self.env['ve.conciliacion.periodo'].sudo()

        # Crear el período que CONTIENE el día de hoy (período activo actual)
        # Si hoy ≤ 15 → período actual = 1Q del mes actual (días 1-15)
        # Si hoy > 15 → período actual = 2Q del mes actual (días 16-fin)
        hoy = _date.today()
        if hoy.day <= 15:
            year, month = hoy.year, hoy.month
            quincena    = '1Q'
            fecha_ini_p = f'{year:04d}-{month:02d}-01'
            fecha_fin_p = f'{year:04d}-{month:02d}-15'
        else:
            year, month = hoy.year, hoy.month
            quincena    = '2Q'
            ld = _cal.monthrange(year, month)[1]
            fecha_ini_p = f'{year:04d}-{month:02d}-16'
            fecha_fin_p = f'{year:04d}-{month:02d}-{ld:02d}'
        periodo_f = f'{year:04d}-{month:02d}'
        periodo_r = f'{periodo_f} {quincena}'

        periodos = Periodo.search([('periodo_retencion', '=', periodo_r)])
        if periodos:
            for p in periodos:
                whs = WhIva.search([('conciliacion_id', '=', p.id)])
                if whs:
                    whs.unlink()
                    log.append(f'✗ {len(whs)} retenciones Odoo eliminadas')
                decl_p = p.declaracion_iva_id
                if decl_p:
                    provs = WhIvaProv.search([('declaracion_iva_id', '=', decl_p.id)])
                else:
                    provs = WhIvaProv.search([('periodo_id', '=', p.id)])
                if provs:
                    provs.unlink()
                    log.append(f'✗ {len(provs)} comprobantes proveedores eliminados')
                seniats = Seniat.search([('conciliacion_id', '=', p.id)])
                if seniats:
                    seniats.unlink()
                    log.append(f'✗ {len(seniats)} retenciones SENIAT eliminadas')
            periodos.unlink()
            log.append(f'✗ {len(periodos)} período(s) eliminado(s)')
        else:
            log.append('— No había período de demo anterior')

        for vat in _DEMO_VATS + _DEMO_PROV_VATS:
            for partner in self.env['res.partner'].sudo().search([('vat', '=', vat)]):
                try:
                    partner.unlink()
                    log.append(f'✗ Partner {vat} eliminado')
                except Exception:
                    log.append(f'↩ Partner {vat} conservado (tiene registros)')

        pids  = self._asegurar_partners(log)
        ppids = self._asegurar_proveedores(log)

        # Las facturas se crean antes que las retenciones para poder vincularlas
        periodo_rec = Periodo.create({
            'name':              periodo_r,
            'periodo':           periodo_f,
            'periodo_retencion': periodo_r,
            'fecha_inicio':      fecha_ini_p,
            'fecha_fin':         fecha_fin_p,
            'estado':            'borrador',
        })
        log.append(f'+ Período {periodo_r} creado ({fecha_ini_p} → {fecha_fin_p})')

        # Desligar retenciones del sistema que se auto-asociaron al crear el período
        stray_whs = WhIva.search([('conciliacion_id', '=', periodo_rec.id)])
        if stray_whs:
            stray_whs.write({'conciliacion_id': False})
            log.append(f'  ↩ {len(stray_whs)} retenciones del sistema desvinculadas')
        stray_sen = Seniat.search([('conciliacion_id', '=', periodo_rec.id)])
        if stray_sen:
            stray_sen.write({'conciliacion_id': False})
            log.append(f'  ↩ {len(stray_sen)} registros SENIAT del sistema desvinculados')

        inv_by_ctrl  = self._crear_facturas_demo(pids, year, month, quincena, log)
        comp_by_ctrl = self._crear_facturas_compra_demo(ppids, year, month, quincena, log)
        self._crear_retenciones_odoo(WhIva, pids, periodo_f, periodo_rec.id, year, month, quincena, log, inv_by_ctrl)
        self._crear_retenciones_seniat(Seniat, periodo_f, periodo_r, periodo_rec.id, year, month, quincena, log)
        self._crear_retenciones_prov(WhIvaProv, ppids, periodo_rec.id, year, month, quincena, log, comp_by_ctrl)
        self._crear_sanciones_demo(log)

        self._asegurar_periodo_hist_declarado(year, month, quincena, log)

        periodo_rec.invalidate_recordset()
        n_wh   = len(periodo_rec.wh_iva_ids)
        n_sen  = len(periodo_rec.seniat_ids)
        n_prov = len(periodo_rec.declaracion_iva_id.wh_iva_prov_ids) if periodo_rec.declaracion_iva_id else 0
        log.append(f'  → Período contiene: {n_wh} ret. Clientes / {n_sen} SENIAT / {n_prov} ret. Proveedores')

        log.append('')
        log.append('Listo. Período activo en Borrador — Dashboard muestra Semáforo y Margen C/D del período actual.')

        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new',
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _asegurar_partners(self, log):
        """Crea/actualiza los partners de demo con es_agente_retencion=True."""
        Partner = self.env['res.partner'].sudo()
        pids = {}
        for name, vat, pct in _DEMO_AGENTES:
            ex = Partner.search([('vat', '=', vat)], limit=1)
            spe_vals = {
                'es_agente_retencion': True,
                'porcentaje_retencion_default': pct,
                'customer_rank': 1,
                # Demo: todos los agentes usan el mismo correo real para que
                # los recordatorios (Lista de Trabajo, Visual IVA Clientes)
                # se puedan probar de punta a punta.
                'email': 'susanaciaval@gmail.com',
            }
            if ex:
                ex.write(spe_vals)
                pids[vat] = ex
                log.append(f'= {name} (actualizado SPE)')
            else:
                pids[vat] = Partner.create({
                    'name': name, 'vat': vat,
                    'company_type': 'company',
                    **spe_vals,
                })
                log.append(f'+ {name}')
        return pids

    def _crear_retenciones_odoo(self, WhIva, pids, periodo_f, conc_id, year, month, quincena, log, inv_by_ctrl=None):
        """
        Crea retenciones Odoo con datos congruentes (reducido a 2 borradores
        en total para agilizar la demo — antes eran 7):
          3 Confirmado  — coinciden exactamente con SENIAT, con factura vinculada
          1 Borrador    — coincide exactamente con SENIAT (doble alícuota)
          1 Borrador    — diferencia de monto respecto al SENIAT
          1 Esperado    — con factura y N° Control, sin comprobante recibido
          1 Vencido     — con factura y N° Control, sin comprobante, venció el plazo
          1 Anulado     — con factura y motivo de anulación
        """
        inv_by_ctrl = inv_by_ctrl or {}
        _day0 = 16 if quincena == '2Q' else 1

        def d(rel):
            return f'{year:04d}-{month:02d}-{_day0 + rel - 1:02d}'

        prev_month  = month - 1 if month > 1 else 12
        prev_year   = year if month > 1 else year - 1
        periodo_ant = f'{prev_year:04d}-{prev_month:02d}'

        company_id = self.env.company.id
        user_id    = self.env.user.id
        yymm       = f'{year}{month:02d}'

        def wh(vat, nro, nro_ctrl, nro_doc, base, fecha, state,
               canal=None, base_red=0.0, porcentaje=75.0):
            iva_std = round(base * 0.16, 2) if base else 0.0
            iva_red = round(base_red * 0.08, 2) if base_red else 0.0
            inv_id  = inv_by_ctrl.get((vat, nro_ctrl or ''), False)
            if inv_id:
                inv_rec = self.env['account.move'].sudo().browse(inv_id)
                nro_doc = inv_rec.name or nro_doc
            vals = {
                'name':                 nro,
                'partner_id':           pids[vat].id,
                'company_id':           company_id,
                'periodo':              periodo_f,
                'fecha':                fecha,
                'invoice_id':           inv_id,
                'nro_control':          nro_ctrl,
                'nro_documento':        nro_doc,
                'tipo_documento':       '01',
                'alicuota':             16.0,
                'monto_base':           base,
                'monto_iva':            iva_std,
                'monto_base_red':       base_red,
                'monto_iva_red':        iva_red,
                'porcentaje_retencion': porcentaje,
                'canal_recepcion':      canal,
                'state':                state,
                'conciliacion_id':      conc_id,
                # C.66 solo activo al CONFIRMAR (no basta con recibir/borrador) —
                # mismo criterio que action_confirmar en ve_wh_iva.py.
                'incluir_declaracion':  state == 'confirmado',
            }
            if state == 'anulado':
                vals['motivo_anulacion'] = (
                    'Comprobante anulado: número de control no corresponde a la factura'
                )
                vals['anulado_por'] = user_id
            return WhIva.create(vals)

        def _marcar_pagada(vat, nro_ctrl):
            """Demo 'Cobranza vs. Comprobante': registra un pago completo sobre la
            factura (vat, nro_ctrl) vía el wizard estándar de Odoo. Aislado en
            try/except para no arriesgar el resto del reset si algo falla."""
            try:
                inv_id = inv_by_ctrl.get((vat, nro_ctrl or ''), False)
                if not inv_id:
                    return
                inv = self.env['account.move'].browse(inv_id)
                if inv.state == 'posted' and inv.payment_state not in ('paid', 'in_payment'):
                    pay_wiz = self.env['account.payment.register'].with_context(
                        active_model='account.move', active_ids=[inv.id],
                    ).create({'payment_date': fields.Date.today()})
                    pay_wiz._create_payments()
            except Exception as exc:
                _logger.warning('Reset Demo: no se pudo registrar el pago demo '
                                 '(%s, %s): %s', vat, nro_ctrl, exc)

        # ── 3 Confirmados (match exacto con SENIAT) — con datos comp_ ──────
        # Alícuota general 16%
        r1 = wh('J-99000001-0', f'{yymm}01000001', '00-0012341', 'A0100001',
                 250_000, d(2), 'confirmado', canal='email')
        r1.write({'comp_base_16': 250_000, 'comp_iva_16': round(250_000 * 0.16, 2),
                  'comp_monto_retenido': round(250_000 * 0.16 * 0.75, 2)})
        # Demo Cobranza vs. Comprobante: "Pagado con Comprobante" (ya confirmado)
        _marcar_pagada('J-99000001-0', '00-0012341')

        # Doble alícuota 16% + 8% (confirmado)
        r2 = wh('J-99000001-0', f'{yymm}01000002', '00-0012342', 'A0100002',
                 180_000, d(3), 'confirmado', canal='whatsapp', base_red=90_000)
        r2.write({'comp_base_16': 180_000, 'comp_iva_16': round(180_000*0.16,2),
                  'comp_base_8':   90_000, 'comp_iva_8':  round(90_000*0.08,2),
                  'comp_monto_retenido': round((180_000*0.16 + 90_000*0.08)*0.75, 2)})
        _marcar_pagada('J-99000001-0', '00-0012342')

        # Alícuota reducida 8% únicamente (confirmado)
        r3 = wh('J-99000002-0', f'{yymm}02000001', '00-0023451', 'B0200001',
                 0, d(5), 'confirmado', canal='directorio', base_red=320_000)
        r3.write({'comp_base_8': 320_000, 'comp_iva_8': round(320_000*0.08,2),
                  'comp_monto_retenido': round(320_000*0.08*0.75, 2)})
        _marcar_pagada('J-99000002-0', '00-0023451')

        # ── 1 Borrador (match exacto) — doble alícuota 16%+8% ────────────
        # Reducido a 2 borradores en total (match + diferencia) a pedido
        # de la usuaria, para agilizar la demo en vivo — antes eran 7.
        r4 = wh('J-99000003-0', f'{yymm}03000001', '00-0034561', 'C0300001',
                 300_000, d(6), 'borrador', canal='email', base_red=150_000)
        r4.write({'comp_base_16': 300_000, 'comp_iva_16': round(300_000*0.16,2),
                  'comp_base_8':  150_000, 'comp_iva_8':  round(150_000*0.08,2),
                  'comp_monto_retenido': round((300_000*0.16+150_000*0.08)*0.75, 2)})
        # Demo Cobranza vs. Comprobante: "Pagado con Comprobante" (recibido, sin confirmar)
        _marcar_pagada('J-99000003-0', '00-0034561')

        # ── 1 Borrador con diferencia de monto (vs SENIAT) ───────────────
        r7 = wh('J-99000002-0', f'{yymm}02000002', '00-0023452', 'B0200002',
                210_000, d(9), 'borrador', canal='email')
        r7.write({'comp_base_16': 210_000, 'comp_iva_16': round(210_000 * 0.16, 2),
                  'comp_monto_retenido': round(210_000 * 0.16 * 0.75, 2)})
        _marcar_pagada('J-99000002-0', '00-0023452')

        def _inv_name(key):
            iid = inv_by_ctrl.get(key, False)
            if iid:
                return self.env['account.move'].sudo().browse(iid).name or ''
            return ''

        # ── 1 Esperado — tiene factura y N° Control, pendiente de recibir ─
        WhIva.create({
            'name':                 False,   # sin N° comprobante — no recibido
            'partner_id':           pids['J-99000001-0'].id,
            'company_id':           company_id,
            'periodo':              periodo_f,
            'invoice_id':           inv_by_ctrl.get(('J-99000001-0', '00-0012345'), False),
            'nro_control':          '00-0012345',
            'nro_documento':        _inv_name(('J-99000001-0', '00-0012345')),
            'tipo_documento':       '01',
            'monto_base':           195_000,
            'monto_iva':            round(195_000 * 0.16, 2),
            'porcentaje_retencion': 75.0,
            'state':                'esperado',
            'incluir_declaracion':  False,
            'conciliacion_id':      conc_id,
        })

        # ── 1 Vencido — factura del período ANTERIOR (ya cerrado), nunca se
        # recibió el comprobante. Se usa una factura del período anterior (no
        # del activo) para que el plazo legal de entrega esté siempre vencido
        # de verdad, sin importar en qué fecha se ejecute el reset: con una
        # factura del período activo, el plazo (2 días hábiles del inicio de
        # la quincena siguiente) puede caer en el futuro y dejar un registro
        # "Vencido" con fecha límite futura — inconsistente, como reportó la
        # usuaria (2026-07-09).
        WhIva.search([
            ('partner_id', '=', pids['J-99000005-0'].id),
            ('periodo', '=', periodo_ant),
            ('conciliacion_id', '=', False),
            ('nro_control', '=', '00-0056782'),
        ]).unlink()
        WhIva.create({
            'name':                 False,   # sin N° comprobante — no recibido
            'partner_id':           pids['J-99000005-0'].id,
            'company_id':           company_id,
            'periodo':              periodo_ant,
            'invoice_id':           inv_by_ctrl.get(('J-99000005-0', '00-0056782'), False),
            'nro_control':          '00-0056782',
            'nro_documento':        _inv_name(('J-99000005-0', '00-0056782')),
            'tipo_documento':       '01',
            'monto_base':           340_000,
            'monto_iva':            round(340_000 * 0.16, 2),
            'porcentaje_retencion': 75.0,
            'state':                'vencido',
            'incluir_declaracion':  False,
            # Sin conciliacion_id → se asignará al período activo al conciliar
        })

        # Demo Cobranza vs. Comprobante: "Pagado sin Comprobante" — nunca recibe
        # comprobante en el flujo de demo, cliente ya pagó hace semanas.
        _marcar_pagada('J-99000005-0', '00-0056782')

        # ── 2 Esperados para demo de recepción por canal ─────────────────────
        # FARMA PLUS — pendiente de recepción manual (PDF de ejemplos/: usar el
        # que corresponda al mes vigente, p.ej. comprobante_demo_manual_julio_1Q.pdf)
        WhIva.create({
            'name':                 False,
            'partner_id':           pids['J-99000004-0'].id,
            'company_id':           company_id,
            'periodo':              periodo_f,
            'invoice_id':           inv_by_ctrl.get(('J-99000004-0', '00-0045673'), False),
            'nro_control':          '00-0045673',
            'tipo_documento':       '01',
            'monto_base':           130_000,
            'monto_iva':            round(130_000 * 0.16, 2),
            'porcentaje_retencion': 75.0,
            'state':                'esperado',
            'incluir_declaracion':  False,
            'conciliacion_id':      conc_id,
            'notas':                'DEMO: recibir manualmente — usar el PDF comprobante_demo_manual_julio_1Q.pdf (o comprobante_demo_manual.pdf)',
        })
        WhIva.create({
            'name':                 False,
            'partner_id':           pids['J-99000005-0'].id,  # SUPERMERCADOS METRO
            'company_id':           company_id,
            'periodo':              periodo_f,
            'invoice_id':           inv_by_ctrl.get(('J-99000005-0', '00-0056783'), False),
            'nro_control':          '00-0056783',
            'nro_documento':        _inv_name(('J-99000005-0', '00-0056783')),
            'tipo_documento':       '01',
            'monto_base':           175_000,
            'monto_iva':            round(175_000 * 0.16, 2),
            'porcentaje_retencion': 75.0,
            'state':                'esperado',
            'incluir_declaracion':  False,
            'conciliacion_id':      conc_id,
            'notas':                'DEMO: recibir por email — enviar comprobante_demo_email_julio_1Q.pdf (o comprobante_demo_email.pdf) al buzón comprobantes-iva@',
        })
        # Demo Cobranza vs. Comprobante: cliente ya pagó, comprobante todavía no
        # llegó — "Pagado sin Comprobante" hasta que se envíe el PDF en la demo.
        _marcar_pagada('J-99000005-0', '00-0056783')
        WhIva.create({
            'name':                 False,
            'partner_id':           pids['J-99000006-0'].id,  # BEBIDAS DEL LLANO
            'company_id':           company_id,
            'periodo':              periodo_f,
            'invoice_id':           inv_by_ctrl.get(('J-99000006-0', '00-0067893'), False),
            'nro_control':          '00-0067893',
            'nro_documento':        _inv_name(('J-99000006-0', '00-0067893')),
            'tipo_documento':       '01',
            'monto_base':           0,
            'monto_iva':            0,
            'monto_base_red':       240_000,
            'monto_iva_red':        round(240_000 * 0.08, 2),
            'porcentaje_retencion': 75.0,
            'state':                'esperado',
            'incluir_declaracion':  False,
            'conciliacion_id':      conc_id,
            'notas':                'DEMO: recibir por WhatsApp — enviar foto de comprobante_demo_whatsapp_julio_1Q.pdf (o comprobante_demo_whatsapp.pdf) al número configurado',
        })
        # Demo Cobranza vs. Comprobante: mismo caso que el de email, por WhatsApp.
        _marcar_pagada('J-99000006-0', '00-0067893')

        # ── 1 Anulado (con factura y motivo) ─────────────────────────────
        wh('J-99000006-0', f'{yymm}06000099', '00-0067899', 'F0600099',
           50_000, d(2), 'anulado', canal='manual')

        # ── 1 Esperado del período ANTERIOR — sin conciliacion_id ───────────
        # Se enganchará al período activo al crear/conciliar, demostrando el arrastre.
        WhIva.search([
            ('partner_id', '=', pids['J-99000002-0'].id),
            ('periodo', '=', periodo_ant),
            ('conciliacion_id', '=', False),
        ]).unlink()
        WhIva.create({
            'name':                 False,   # sin N° comprobante — no recibido
            'partner_id':           pids['J-99000002-0'].id,
            'company_id':           company_id,
            'periodo':              periodo_ant,
            'invoice_id':           inv_by_ctrl.get(('J-99000002-0', '00-0023400'), False),
            'nro_control':          '00-0023400',
            'nro_documento':        _inv_name(('J-99000002-0', '00-0023400')),
            'tipo_documento':       '01',
            'monto_base':           160_000,
            'monto_iva':            round(160_000 * 0.16, 2),
            'porcentaje_retencion': 75.0,
            'state':                'esperado',
            'incluir_declaracion':  False,
            # Sin conciliacion_id → se asignará al período activo
        })

        log.append(
            '+ 11 retenciones Odoo creadas  '
            '(3 confirmado · 1 borrador-match · 1 borrador-dif · '
            '1 esperado · 1 vencido · 1 anulado · '
            '+3 esperado canal manual/email/whatsapp)'
        )
        log.append(
            f'+ 1 retención No Recibida y 1 Vencida del período anterior '
            f'({periodo_ant}) — demuestran arrastre'
        )

    def _crear_sanciones_demo(self, log):
        """Crea 3 sanciones IVA de demo con líneas:
          1 Pendiente  — ilícito formal (comprobantes no entregados)
          2 Impugnada  — omisión de declaración, recurso presentado
          3 Pagada     — multa formal ya liquidada (no suma en KPI 4)
        """
        from datetime import date as _date
        Sancion = self.env['ve.sancion.iva'].sudo()

        # Limpiar sanciones de demo anteriores (identificadas por prefijo en notas)
        demos = Sancion.search([('note', 'like', 'DEMO:')])
        if demos:
            demos.unlink()
            log.append(f'✗ {len(demos)} sanciones demo anteriores eliminadas')

        # Tasa EUR/BCV: buscar la más reciente o usar valor de referencia
        rate_rec = self.env['res.currency.rate'].sudo().search([
            ('currency_id.name', '=', 'EUR'),
            ('company_id', '=', self.env.company.id),
        ], order='name desc', limit=1)
        tasa_eur = rate_rec.rate if rate_rec and rate_rec.rate else 100.0

        hoy = _date.today()

        # ── Sanción 1: Pendiente — ilícito formal ─────────────────────────────
        fecha_1 = hoy.replace(month=hoy.month - 2 if hoy.month > 2 else hoy.month + 10,
                              year=hoy.year if hoy.month > 2 else hoy.year - 1)
        Sancion.create({
            'name': 'Resolución SNAT — Comprobantes No Entregados en Plazo',
            'numero_resolucion': 'SNAT/2026/DEMO-001',
            'fecha': fecha_1,
            'tipo_origen': 'periodo_especifico',
            'es_agente_retencion': True,
            'estado': 'pendiente',
            'fecha_vencimiento_pago': fecha_1.replace(
                month=fecha_1.month + 1 if fecha_1.month < 12 else 1,
                year=fecha_1.year if fecha_1.month < 12 else fecha_1.year + 1,
            ),
            'note': (
                'DEMO: Sanción por ilícito formal.\n'
                '3 comprobantes de retención IVA no entregados dentro del plazo '
                'legal establecido en la PA SNAT/2025/000054.\n'
                'Monto estimado a la tasa EUR BCV del día de la resolución.'
            ),
            'line_ids': [(0, 0, {
                'tipo': 'ilicito_formal',
                'descripcion': '3 comprobantes no entregados en plazo — período demo',
                'cantidad': 3,
                'monto_bs': round(3 * 500_000, 2),
                'tasa_eur_bcv': tasa_eur,
            })],
        })

        # ── Sanción 2: Impugnada — omisión + interés moratorio ───────────────
        fecha_2 = hoy.replace(month=hoy.month - 5 if hoy.month > 5 else hoy.month + 7,
                              year=hoy.year if hoy.month > 5 else hoy.year - 1)
        Sancion.create({
            'name': 'Acta de Reparo SENIAT — Omisión de Declaración IVA',
            'numero_resolucion': 'SNAT/2025/DEMO-002',
            'fecha': fecha_2,
            'tipo_origen': 'auditoria_fiscal',
            'es_agente_retencion': True,
            'estado': 'impugnada',
            'note': (
                'DEMO: Sanción impugnada mediante recurso jerárquico.\n'
                'Recurso presentado ante la Gerencia Regional del SENIAT en fecha '
                f'{fecha_2}. Número de expediente: EXP-DEMO-2025-001.\n'
                'Fundamento: error de hecho y de derecho en la determinación '
                'de la base imponible — período auditado no correspondía a agente SPE.'
            ),
            'line_ids': [
                (0, 0, {
                    'tipo': 'omision',
                    'descripcion': 'Omisión declaración IVA — base imponible estimada por SENIAT',
                    'cantidad': 1,
                    'monto_bs': 8_750_000.0,
                    'tasa_eur_bcv': tasa_eur,
                }),
                (0, 0, {
                    'tipo': 'interes_moratorio',
                    'descripcion': 'Interés moratorio Art. 66 COT — 180 días a tasa activa BCV',
                    'cantidad': 1,
                    'monto_bs': 1_200_000.0,
                    'tasa_eur_bcv': tasa_eur,
                }),
            ],
        })

        # ── Sanción 3: Pagada — multa formal ya liquidada ─────────────────────
        fecha_3 = hoy.replace(month=hoy.month - 9 if hoy.month > 9 else hoy.month + 3,
                              year=hoy.year if hoy.month > 9 else hoy.year - 1)
        Sancion.create({
            'name': 'Multa Formal — Presentación Tardía Declaración IVA',
            'numero_resolucion': 'SNAT/2025/DEMO-003',
            'fecha': fecha_3,
            'tipo_origen': 'autoliquidacion',
            'es_agente_retencion': True,
            'estado': 'pagada',
            'fecha_vencimiento_pago': fecha_3,
            'fecha_pago': fecha_3,
            'note': (
                'DEMO: Sanción pagada — liquidada mediante planilla de autoliquidación.\n'
                'Presentación tardía de la declaración IVA período anterior. '
                'Monto pagado según planilla N° PL-DEMO-2025-001.\n'
                'Esta sanción NO suma en el KPI 4 del Dashboard (estado: pagada).'
            ),
            'line_ids': [(0, 0, {
                'tipo': 'multa_forma',
                'descripcion': 'Multa por presentación tardía de declaración IVA — Art. 100 COT',
                'cantidad': 1,
                'monto_bs': 350_000.0,
                'tasa_eur_bcv': tasa_eur,
            })],
        })

        log.append(
            '+ 3 sanciones IVA demo creadas  '
            '(1 pendiente ilícito formal · 1 impugnada omisión+interés · 1 pagada multa forma)'
        )

    def _crear_retenciones_seniat(self, Seniat, periodo_f, periodo_r, conc_id, year, month, quincena, log):
        """
        Crea 10 retenciones SENIAT:
          6 match exacto con Odoo
          2 con diferencia de monto respecto a Odoo
          2 solo en SENIAT (sin retención en Odoo)
        """
        _day0 = 16 if quincena == '2Q' else 1

        def d(rel):
            return f'{year:04d}-{month:02d}-{_day0 + rel - 1:02d}'

        def sen(rif, nombre, nro_ctrl, nro_doc, base, monto_ret, alicuota=16.0):
            Seniat.create({
                'rif_agente':     rif,
                'nombre_agente':  nombre,
                'nro_control':    nro_ctrl,
                'nro_documento':  nro_doc,
                'tipo_documento': '01',
                'periodo':        periodo_f,
                'periodo_retencion': periodo_r,
                'fecha':          d(12),
                'monto_base':     base,
                'monto_retenido': monto_ret,
                'alicuota':       alicuota,
                'conciliacion_id': conc_id,
                'cargado_por_rpa': True,
            })

        # ── 5 match exacto (mismo nro_control que Odoo) ───────────────────
        # Distribuidora: 16%
        sen('J-99000001-0', 'DISTRIBUIDORA CENTRAL CA',    '00-0012341', 'A0100001', 250_000,  round(250_000*0.16*0.75, 2))
        # Distribuidora: 16%+8%
        sen('J-99000001-0', 'DISTRIBUIDORA CENTRAL CA',    '00-0012342', 'A0100002', 270_000,  round((180_000*0.16+90_000*0.08)*0.75, 2))
        # Suministros: 8% pura (alícuota reducida)
        sen('J-99000002-0', 'SUMINISTROS INDUSTRIALES CA', '00-0023451', 'B0200001', 320_000,  round(320_000*0.08*0.75, 2), alicuota=8.0)
        # Servicios Digitales: 16%+8%
        sen('J-99000003-0', 'SERVICIOS DIGITALES SA',      '00-0034561', 'C0300001', 450_000,  round((300_000*0.16+150_000*0.08)*0.75, 2))

        # ── 1 Esperado con match en SENIAT (No Recibido físico pero sí en SENIAT) ──
        sen('J-99000001-0', 'DISTRIBUIDORA CENTRAL CA',    '00-0012345', 'A0100003', 195_000,  round(195_000 * 0.16 * 0.75, 2))

        # ── 1 con diferencia de monto ─────────────────────────────────────
        sen('J-99000002-0', 'SUMINISTROS INDUSTRIALES CA', '00-0023452', 'B0200002', 210_000,  20_000)

        # ── 2 solo en SENIAT (sin retención registrada en Odoo) ──────────
        sen('J-99000001-0', 'DISTRIBUIDORA CENTRAL CA',    '00-0012999', 'A0199001',  90_000,  10_800)
        sen('J-99000002-0', 'SUMINISTROS INDUSTRIALES CA', '00-0023999', 'B0299001',  75_000,   9_000)

        log.append('+ 8 retenciones SENIAT creadas  (5 match · 1 dif · 2 solo-SENIAT)')

    def _crear_facturas_demo(self, pids, year, month, quincena, log):
        """Crea facturas de venta confirmadas (una por retención Odoo) con líneas
        correctas por alícuota (16% / 8%) dentro de la quincena del período.
        Retorna dict {(vat, nro_ctrl): invoice_id}.
        """
        company = self.env.company
        Move = self.env['account.move'].sudo()

        import calendar as _cal
        fecha_ini = f'{year:04d}-{month:02d}-01'
        fecha_fin = f'{year:04d}-{month:02d}-{_cal.monthrange(year, month)[1]:02d}'

        # Limpiar facturas demo anteriores de estos agentes en el período
        # Limpiar facturas sin N° Control de demos anteriores (cualquier período)
        old_no_ctrl = Move.search([
            ('partner_id.vat', 'in', _DEMO_VATS),
            ('move_type', '=', 'out_invoice'),
            ('nro_control', '=', False),
        ])
        for inv in old_no_ctrl:
            try:
                if inv.state == 'posted':
                    inv.button_draft()
                inv.unlink()
            except Exception as e:
                log.append(f'  ↩ {inv.name}: sin N°Control no eliminada — {e}')
        if old_no_ctrl:
            log.append(f'✗ {len(old_no_ctrl)} facturas sin N°Control eliminadas')

        old = Move.search([
            ('partner_id.vat', 'in', _DEMO_VATS),
            ('move_type', '=', 'out_invoice'),
            ('invoice_date', '>=', fecha_ini),
            ('invoice_date', '<=', fecha_fin),
        ])
        if old:
            for inv in old:
                try:
                    if inv.state == 'posted':
                        inv.button_draft()
                    inv.unlink()
                except Exception as e:
                    log.append(f'  ↩ {inv.name or inv.partner_id.name}: no eliminada — {e}')
            log.append(f'✗ {len(old)} facturas demo anteriores eliminadas')

        journal = self.env['account.journal'].sudo().search([
            ('type', '=', 'sale'), ('company_id', '=', company.id),
        ], limit=1)
        if not journal:
            log.append('⚠ Sin diario de ventas — facturas demo omitidas')
            return {}

        tax_16 = self.env['account.tax'].sudo().search([
            ('type_tax_use', '=', 'sale'), ('amount', '=', 16.0),
            ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
        ], limit=1)
        tax_8 = self.env['account.tax'].sudo().search([
            ('type_tax_use', '=', 'sale'), ('amount', '=', 8.0),
            ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
        ], limit=1)
        if not tax_8 and tax_16:
            try:
                tax_8 = tax_16.copy({
                    'name': 'IVA 8% Ventas (Alícuota Reducida) — Demo',
                    'amount': 8.0,
                })
                log.append('+ IVA 8% creado (clon de IVA 16%) para líneas de alícuota reducida')
            except Exception as e:
                log.append(f'⚠ No se pudo crear IVA 8%: {e} — líneas reducidas sin impuesto')

        income_acct = self.env['account.account'].sudo().search([
            ('account_type', 'in', ('income', 'income_other')),
            ('company_ids', 'in', [company.id]),
        ], limit=1)
        if not income_acct:
            log.append('⚠ Sin cuenta de ingresos — facturas demo omitidas')
            return {}

        _day0 = 16 if quincena == '2Q' else 1

        def d(rel):
            return f'{year:04d}-{month:02d}-{_day0 + rel - 1:02d}'

        # (vat, base_16, base_8, nro_ctrl, dia_relativo, descripción)
        # base_16 → línea con IVA 16%; base_8 → línea con IVA 8%
        datos = [
            # Confirmados
            ('J-99000001-0', 250_000,       0, '00-0012341', 2,  'Consultoría'),
            ('J-99000001-0', 180_000,  90_000, '00-0012342', 3,  'Asesoría — 16%+8%'),
            ('J-99000002-0',       0, 320_000, '00-0023451', 5,  'Suministro de materiales — 8%'),
            # Borrador match (reducido a 2 borradores en total — match + diferencia)
            ('J-99000003-0', 300_000, 150_000, '00-0034561', 6,  'Servicios de tecnología — 16%+8%'),
            # Borrador diferencia
            ('J-99000002-0', 210_000,       0, '00-0023452', 9,  'Repuestos industriales'),
            # Esperado (tiene factura y N° Control, comprobante aún no recibido)
            ('J-99000001-0', 195_000,       0, '00-0012345', 4,  'Consultoría — pendiente comp.'),
            # Nota: la factura del comprobante "Vencido" (00-0056782) se crea
            # más abajo con fecha del período ANTERIOR, no aquí — así el plazo
            # de entrega ya está vencido de verdad sin importar cuándo se
            # ejecute el reset (ver bloque "Factura período anterior").
            # Anulado (factura existe, comp. fue anulado por error)
            ('J-99000006-0',  50_000,       0, '00-0067899', 2,  'Servicios menores — comp. anulado'),
            # ── 3 Esperados para prueba de recepción por canal ─────────────
            ('J-99000004-0', 130_000,       0, '00-0045673', 3,  'Farmacéuticos — pendiente MANUAL'),
            ('J-99000005-0', 175_000,       0, '00-0056783', 5,  'Alimentos D — pendiente EMAIL'),
            ('J-99000006-0',       0, 240_000, '00-0067893', 11, 'Servicios esp. — pendiente WHATSAPP'),
        ]

        inv_by_ctrl = {}   # {(vat, nro_ctrl or ''): invoice.id}
        creadas = self.env['account.move'].sudo()
        for vat, base_16, base_8, nro_ctrl, dia_rel, desc in datos:
            partner = pids.get(vat)
            if not partner:
                continue
            lines = []
            if base_16 > 0:
                l16 = {'name': desc, 'quantity': 1,
                       'price_unit': base_16, 'account_id': income_acct.id}
                if tax_16:
                    l16['tax_ids'] = [(6, 0, [tax_16.id])]
                lines.append((0, 0, l16))
            if base_8 > 0:
                l8 = {'name': f'{desc} (8%)', 'quantity': 1,
                      'price_unit': base_8, 'account_id': income_acct.id}
                if tax_8:
                    l8['tax_ids'] = [(6, 0, [tax_8.id])]
                lines.append((0, 0, l8))
            if not lines:
                continue
            vals = {
                'move_type': 'out_invoice', 'partner_id': partner.id,
                'invoice_date': d(dia_rel), 'invoice_date_due': d(dia_rel),
                'journal_id': journal.id,
                'company_id': company.id, 'invoice_line_ids': lines,
            }
            if nro_ctrl:
                vals['nro_control'] = nro_ctrl
            try:
                inv = Move.create(vals)
                creadas |= inv
                inv_by_ctrl[(vat, nro_ctrl or '')] = inv.id
            except Exception as e:
                if nro_ctrl:
                    existing = Move.search([
                        ('partner_id', '=', partner.id),
                        ('move_type', '=', 'out_invoice'),
                        ('nro_control', '=', nro_ctrl),
                    ], limit=1)
                    if existing:
                        inv_by_ctrl[(vat, nro_ctrl)] = existing.id
                        log.append(f'  ↩ Reutilizando {existing.name} ({nro_ctrl})')
                    else:
                        log.append(f'  ⚠ Error creando factura {vat}/{nro_ctrl}: {e}')
                else:
                    log.append(f'  ⚠ Error creando factura {vat}: {e}')

        if not creadas:
            return inv_by_ctrl

        confirmadas = 0
        for inv in creadas:
            try:
                inv.action_post()
                confirmadas += 1
            except Exception as e:
                log.append(f'  ⚠ Error confirmando {inv.partner_id.name}: {e}')

        # Eliminar retenciones auto-creadas por action_post; los datos demo correctos
        # los crea _crear_retenciones_odoo a continuación.
        auto_whs = self.env['ve.wh.iva'].sudo().search([
            ('invoice_id', 'in', creadas.ids),
        ])
        if auto_whs:
            auto_whs.unlink()
            log.append(f'  ↩ {len(auto_whs)} retenciones auto-creadas eliminadas (se recrearán con datos demo)')

        tax_msg = ''
        if tax_16 and tax_8:
            tax_msg = ' IVA 16%+8%'
        elif tax_16:
            tax_msg = f' IVA {tax_16.amount:.0f}% (sin IVA 8%)'
        else:
            tax_msg = ' (sin impuesto — configura IVA 16%)'
        log.append(
            f'+ {len(creadas)} facturas demo creadas, '
            f'{confirmadas} confirmadas{tax_msg}'
        )

        # ── Factura del período anterior (demuestra arrastre de pendientes) ──
        prev_month = month - 1 if month > 1 else 12
        prev_year  = year if month > 1 else year - 1
        partner_prev = pids.get('J-99000002-0')
        if partner_prev and journal and income_acct:
            old_prev = Move.search([
                ('partner_id', '=', partner_prev.id),
                ('move_type', '=', 'out_invoice'),
                ('nro_control', '=', '00-0023400'),
            ])
            for inv_old in old_prev:
                try:
                    if inv_old.state == 'posted':
                        inv_old.button_draft()
                    inv_old.unlink()
                except Exception:
                    pass
            try:
                line_prev = {
                    'name': f'Suministro — período anterior {prev_year:04d}-{prev_month:02d} (comp. pendiente)',
                    'quantity': 1, 'price_unit': 160_000, 'account_id': income_acct.id,
                }
                if tax_16:
                    line_prev['tax_ids'] = [(6, 0, [tax_16.id])]
                inv_prev = Move.create({
                    'move_type': 'out_invoice', 'partner_id': partner_prev.id,
                    'invoice_date': f'{prev_year:04d}-{prev_month:02d}-20',
                    'invoice_date_due': f'{prev_year:04d}-{prev_month:02d}-20',
                    'journal_id': journal.id, 'company_id': company.id,
                    'nro_control': '00-0023400',
                    'invoice_line_ids': [(0, 0, line_prev)],
                })
                inv_prev.action_post()
                auto_prev = self.env['ve.wh.iva'].sudo().search([('invoice_id', '=', inv_prev.id)])
                if auto_prev:
                    auto_prev.unlink()
                inv_by_ctrl[('J-99000002-0', '00-0023400')] = inv_prev.id
                log.append(f'+ Factura período anterior ({prev_year:04d}-{prev_month:02d}) creada para arrastre')
            except Exception as e:
                log.append(f'  ⚠ Error creando factura período anterior: {e}')

        # ── Factura del período anterior para el comprobante "Vencido" ──────
        # Se usa una factura ya cerrada (no del período activo) para que el
        # plazo legal de entrega esté siempre vencido de verdad, sin importar
        # la fecha en que se ejecute el reset.
        partner_venc = pids.get('J-99000005-0')
        if partner_venc and journal and income_acct:
            old_venc = Move.search([
                ('partner_id', '=', partner_venc.id),
                ('move_type', '=', 'out_invoice'),
                ('nro_control', '=', '00-0056782'),
            ])
            for inv_old in old_venc:
                try:
                    if inv_old.state == 'posted':
                        inv_old.button_draft()
                    inv_old.unlink()
                except Exception:
                    pass
            try:
                line_venc = {
                    'name': f'Alimentos B — período anterior {prev_year:04d}-{prev_month:02d} (comp. vencido)',
                    'quantity': 1, 'price_unit': 340_000, 'account_id': income_acct.id,
                }
                if tax_16:
                    line_venc['tax_ids'] = [(6, 0, [tax_16.id])]
                inv_venc = Move.create({
                    'move_type': 'out_invoice', 'partner_id': partner_venc.id,
                    'invoice_date': f'{prev_year:04d}-{prev_month:02d}-08',
                    'invoice_date_due': f'{prev_year:04d}-{prev_month:02d}-08',
                    'journal_id': journal.id, 'company_id': company.id,
                    'nro_control': '00-0056782',
                    'invoice_line_ids': [(0, 0, line_venc)],
                })
                inv_venc.action_post()
                auto_venc = self.env['ve.wh.iva'].sudo().search([('invoice_id', '=', inv_venc.id)])
                if auto_venc:
                    auto_venc.unlink()
                inv_by_ctrl[('J-99000005-0', '00-0056782')] = inv_venc.id
                log.append(f'+ Factura período anterior ({prev_year:04d}-{prev_month:02d}) creada para comprobante Vencido')
            except Exception as e:
                log.append(f'  ⚠ Error creando factura período anterior (Vencido): {e}')

        return inv_by_ctrl

    def _asegurar_proveedores(self, log):
        """Crea/actualiza los partners proveedores de demo."""
        Partner = self.env['res.partner'].sudo()
        ppids = {}
        for name, vat, tiene_ctrl in _DEMO_PROVEEDORES:
            ex = Partner.search([('vat', '=', vat)], limit=1)
            vals = {'supplier_rank': 1, 'email': 'susanaciaval@gmail.com'}
            if ex:
                ex.write(vals)
                ppids[vat] = ex
                log.append(f'= {name} (proveedor, actualizado)')
            else:
                ppids[vat] = Partner.create({
                    'name': name, 'vat': vat,
                    'company_type': 'company',
                    **vals,
                })
                log.append(f'+ {name} (proveedor)')
        return ppids

    def _crear_facturas_compra_demo(self, ppids, year, month, quincena, log):
        """Crea facturas de compra confirmadas para los proveedores de demo.
        Retorna dict {(vat, nro_ctrl): invoice_id}."""
        company = self.env.company
        Move    = self.env['account.move'].sudo()
        _day0   = 16 if quincena == '2Q' else 1

        def d(rel):
            return f'{year:04d}-{month:02d}-{_day0 + rel - 1:02d}'

        import calendar as _cal
        fecha_ini = f'{year:04d}-{month:02d}-01'
        fecha_fin = f'{year:04d}-{month:02d}-{_cal.monthrange(year, month)[1]:02d}'

        old = Move.search([
            ('partner_id.vat', 'in', _DEMO_PROV_VATS),
            ('move_type', '=', 'in_invoice'),
            ('invoice_date', '>=', fecha_ini),
            ('invoice_date', '<=', fecha_fin),
        ])
        for inv in old:
            try:
                if inv.state == 'posted':
                    inv.button_draft()
                inv.unlink()
            except Exception as e:
                log.append(f'  ↩ {inv.name}: no eliminada — {e}')
        if old:
            log.append(f'✗ {len(old)} facturas compra demo anteriores eliminadas')

        journal = self.env['account.journal'].sudo().search([
            ('type', '=', 'purchase'), ('company_id', '=', company.id),
        ], limit=1)
        if not journal:
            log.append('⚠ Sin diario de compras — facturas proveedores omitidas')
            return {}

        tax_16 = self.env['account.tax'].sudo().search([
            ('type_tax_use', '=', 'purchase'), ('amount', '=', 16.0),
            ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
        ], limit=1)
        tax_8 = self.env['account.tax'].sudo().search([
            ('type_tax_use', '=', 'purchase'), ('amount', '=', 8.0),
            ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
        ], limit=1)
        if not tax_8 and tax_16:
            try:
                tax_8 = tax_16.copy({
                    'name': 'IVA 8% Compras (Alícuota Reducida) — Demo',
                    'amount': 8.0,
                })
                log.append('+ IVA 8% Compras creado para demo')
            except Exception as e:
                log.append(f'⚠ No se pudo crear IVA 8% Compras: {e}')

        # currency_id=False descarta cuentas que obligan moneda secundaria
        # (mismo problema que causaba el error de asientos con VEF/moneda
        # inactiva en las facturas de venta — ver _crear_facturas_demo).
        exp_acct = self.env['account.account'].sudo().search([
            ('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost')),
            ('company_ids', 'in', [company.id]), ('currency_id', '=', False),
        ], limit=1)
        if not exp_acct:
            log.append('⚠ Sin cuenta de gastos — facturas proveedores omitidas')
            return {}

        # (vat, base_16, base_8, nro_ctrl, dia_rel, desc, ref_prov)
        datos = [
            ('J-88000001-0', 200_000,      0, 'NC-16-001', 2,  'Servicios tecnología',        'FACT-2026-001'),
            ('J-88000001-0', 150_000, 80_000, 'NC-16-002', 4,  'Asesoría + soporte reducido', 'FACT-2026-002'),
            ('J-88000002-0',       0,300_000, 'NC-16-003', 6,  'Materiales alícuota 8%',      'FACT-2026-010'),
            ('J-88000003-0',  50_000,      0, '',           8,  'Papelería (sin N° Control)',  'FACT-2026-020'),
            ('J-88000002-0', 120_000,      0, 'NC-16-004', 10, 'Insumos varios',              'FACT-2026-011'),
        ]

        comp_by_ctrl = {}
        creadas = self.env['account.move'].sudo()
        for vat, base_16, base_8, nro_ctrl, dia_rel, desc, ref_prov in datos:
            partner = ppids.get(vat)
            if not partner:
                continue
            lines = []
            if base_16 > 0:
                l = {'name': desc, 'quantity': 1,
                     'price_unit': base_16, 'account_id': exp_acct.id}
                if tax_16:
                    l['tax_ids'] = [(6, 0, [tax_16.id])]
                lines.append((0, 0, l))
            if base_8 > 0:
                l = {'name': f'{desc} (8%)', 'quantity': 1,
                     'price_unit': base_8, 'account_id': exp_acct.id}
                if tax_8:
                    l['tax_ids'] = [(6, 0, [tax_8.id])]
                lines.append((0, 0, l))
            if not lines:
                continue
            vals = {
                'move_type':        'in_invoice',
                'partner_id':       partner.id,
                'invoice_date':     d(dia_rel),
                'journal_id':       journal.id,
                'company_id':       company.id,
                'currency_id':      company.currency_id.id,
                'ref':              ref_prov,
                'invoice_line_ids': lines,
            }
            if nro_ctrl:
                vals['nro_control'] = nro_ctrl
            try:
                inv = Move.create(vals)
                creadas |= inv
                comp_by_ctrl[(vat, nro_ctrl)] = inv.id
            except Exception as e:
                log.append(f'  ⚠ Error creando factura compra {vat}/{nro_ctrl}: {e}')

        confirmadas = 0
        for inv in creadas:
            try:
                inv.action_post()
                confirmadas += 1
            except Exception as e:
                log.append(f'  ⚠ Error confirmando factura compra {inv.partner_id.name} (queda en Borrador, no Registrado): {e}')

        log.append(
            f'+ {len(creadas)} facturas compra demo creadas, {confirmadas} confirmadas'
        )
        return comp_by_ctrl

    def _crear_retenciones_prov(self, WhIvaProv, ppids, conc_id, year, month, quincena, log, comp_by_ctrl=None):
        """Crea 5 comprobantes ve.wh.iva.prov en estados variados:
          1 borrador — base 16%,     75%   (SOLUCIONES TECH, con N° Control)
          1 enviado  — base 16%+8%,  75%   (SOLUCIONES TECH, doble alícuota)
          1 enviado  — base 8%,      75%   (MATERIALES Y SERVICIOS)
          1 borrador — base 16%,    100%   (PAPELERIA EL SOL, sin N° Control)
          1 borrador — base 16%,     75%   (MATERIALES Y SERVICIOS)
        Todos vinculados a su factura de compra → Libro Compras sin filas sin retención.
        """
        comp_by_ctrl = comp_by_ctrl or {}
        _day0 = 16 if quincena == '2Q' else 1

        def d(rel):
            return f'{year:04d}-{month:02d}-{_day0 + rel - 1:02d}'

        now     = fields.Datetime.now()
        user_id = self.env.user.id

        # (vat, nro_ctrl, base_16, base_8, pct, dia_rel, state)
        # PAPELERIA EL SOL usa nro_ctrl='' (sin N° Control) → 100% retención (PA SNAT/2025/000054)
        datos = [
            ('J-88000001-0', 'NC-16-001', 200_000,       0,  75.0,  2, 'borrador'),
            ('J-88000001-0', 'NC-16-002', 150_000,  80_000,  75.0,  4, 'enviado'),
            ('J-88000002-0', 'NC-16-003',       0, 300_000,  75.0,  6, 'enviado'),
            ('J-88000003-0', '',           50_000,       0, 100.0,  8, 'borrador'),
            ('J-88000002-0', 'NC-16-004', 120_000,       0,  75.0, 10, 'borrador'),
        ]

        creados = 0
        for vat, nro_ctrl, base_16, base_8, pct, dia_rel, state in datos:
            partner = ppids.get(vat)
            if not partner:
                continue
            inv_id = comp_by_ctrl.get((vat, nro_ctrl), False)
            vals = {
                'partner_id':           partner.id,
                'invoice_id':           inv_id,
                'nro_control':          nro_ctrl or False,
                'fecha':                d(dia_rel),
                'monto_base_16':        base_16,
                'monto_base_8':         base_8,
                'porcentaje_retencion': pct,
                'state':                state,
                'declaracion_iva_id':   self.env['ve.declaracion.iva'].sudo()._get_or_create_for_periodo(conc_id).id,
            }
            if state in ('enviado', 'declarado'):
                vals['fecha_envio']    = now
                vals['enviado_por_id'] = user_id
            if state == 'declarado':
                vals['declarado_por_id']  = user_id
                vals['fecha_declaracion'] = now
            try:
                WhIvaProv.create(vals)
                creados += 1
            except Exception as e:
                log.append(f'  ⚠ Error creando ret. prov {vat}/{nro_ctrl}: {e}')

        log.append(
            f'+ {creados} comprobantes IVA Proveedores creados  '
            f'(2 borrador · 2 enviado · 1 anulado)'
        )

    def _asegurar_compra_excedente_hist(self, h_r, h_fin, company_id, log):
        """Crea (si no existe) una factura de compra grande en el período
        histórico para que quede con excedente real de crédito fiscal —
        pedido explícito de la usuaria (2026-07-24) para poder probar en QA
        el escenario "excedente de crédito fiscal + retenciones acumuladas"
        del período anterior al reiniciar el demo.

        No son dos escenarios independientes: cuando el crédito fiscal
        (C.39) supera al débito (C.49), la cuota a pagar (C.78) da 0 — y
        como no queda nada contra qué aplicar retenciones, TODA la
        retención soportada ese período también queda sin usar y arrastra
        como C.54. Una sola factura de compra grande alcanza para generar
        ambos excedentes a la vez en el período histórico.
        """
        Move = self.env['account.move'].sudo()
        ref = f'DEMO-HIST-EXCEDENTE-{h_r}'
        if Move.search([('ref', '=', ref), ('move_type', '=', 'in_invoice')], limit=1):
            return
        journal = self.env['account.journal'].sudo().search(
            [('type', '=', 'purchase'), ('company_id', '=', company_id)], limit=1)
        exp_acct = self.env['account.account'].sudo().search(
            [('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost')),
             ('company_ids', 'in', [company_id]), ('currency_id', '=', False)], limit=1)
        tax_16 = self.env['account.tax'].sudo().search(
            [('type_tax_use', '=', 'purchase'), ('amount', '=', 16.0),
             ('amount_type', '=', 'percent'), ('company_id', '=', company_id)], limit=1)
        prov = self.env['res.partner'].sudo().search(
            [('vat', '=', _DEMO_PROV_VATS[0])], limit=1) if _DEMO_PROV_VATS else False
        if not (journal and exp_acct and tax_16 and prov):
            log.append('  ⚠ Sin diario/cuenta/impuesto/proveedor de compra — '
                        'excedente crédito fiscal histórico omitido')
            return
        compra = Move.create({
            'move_type':    'in_invoice',
            'partner_id':   prov.id,
            'invoice_date': h_fin,
            'journal_id':   journal.id,
            'company_id':   company_id,
            'ref':          ref,
            'invoice_line_ids': [(0, 0, {
                'name':       f'Compra grande — genera excedente crédito fiscal ({h_r})',
                'quantity':   1,
                'price_unit': 700_000,
                'account_id': exp_acct.id,
                'tax_ids':    [(6, 0, [tax_16.id])],
            })],
        })
        try:
            compra.action_post()
            log.append(f'+ Factura de compra histórica ({h_r}) — genera excedente '
                        'de crédito fiscal Y retenciones acumuladas')
        except Exception as e:
            log.append(f'  ⚠ Factura de compra histórica ({h_r}): {e}')

    def _asegurar_periodo_hist_declarado(self, year, month, quincena, log):
        """Garantiza que exista un período declarado previo al activo (historial demo).
        Si el período ya existe NO lo toca — solo crea si está completamente ausente.
        Solo repara la Declaración IVA si el período existe pero le falta."""
        import calendar as _cal
        Periodo     = self.env['ve.conciliacion.periodo'].sudo()
        WhIva       = self.env['ve.wh.iva'].sudo()
        Declaracion = self.env['ve.declaracion.iva'].sudo()
        company_id  = self.env.company.id

        h_month = month - 1 if month > 1 else 12
        h_year  = year if month > 1 else year - 1
        h_f   = f'{h_year:04d}-{h_month:02d}'
        h_r   = f'{h_f} 2Q'
        ld    = _cal.monthrange(h_year, h_month)[1]
        h_ini = f'{h_year:04d}-{h_month:02d}-16'
        h_fin = f'{h_year:04d}-{h_month:02d}-{ld:02d}'

        hist = Periodo.search([('periodo_retencion', '=', h_r)], limit=1)

        if hist:
            # Período existe — solo reparar lo que falte
            decl = Declaracion.search([('conciliacion_id', '=', hist.id)], limit=1)
            if not decl:
                decl = Declaracion._get_or_create_for_periodo(hist.id)
                if decl.estado not in ('presentada', 'sustitutiva'):
                    decl.write({'estado': 'presentada', 'fecha_declaracion': fields.Datetime.now()})
                if hist.estado not in ('aprobado', 'declarado'):
                    hist.write({'estado': 'declarado'})
                log.append(f'✓ Período {h_r}: Declaración IVA recreada')
            else:
                log.append(f'✓ Período histórico {h_r} sin cambios')
            # Reparar IVA Proveedores si falta y el período está declarado
            if decl and hist.estado == 'declarado' and decl.estado_prov != 'declarado':
                activos_prov = decl.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
                if not activos_prov:
                    ppids = self._asegurar_proveedores([])
                    self._crear_prov_hist_declarado(decl, ppids, h_year, h_month, log)
            # Idempotente: si el período ya existe de una corrida vieja (sin
            # la factura de excedente), se completa aquí también.
            self._asegurar_compra_excedente_hist(h_r, h_fin, company_id, log)
            return

        # El período no existe — crearlo con datos mínimos de demo
        hist = Periodo.create({
            'name':              h_r,
            'periodo':           h_f,
            'periodo_retencion': h_r,
            'fecha_inicio':      h_ini,
            'fecha_fin':         h_fin,
            'estado':            'borrador',
        })
        log.append(f'+ Período histórico {h_r} creado ({h_ini} → {h_fin})')

        demo_hist = [
            ('J-99000001-0', 180_000, 75.0),
            ('J-99000002-0',  95_000, 75.0),
            ('J-99000003-0', 250_000, 75.0),
        ]
        partners = self.env['res.partner'].sudo().search(
            [('vat', 'in', [v for v, _, _ in demo_hist])])
        pmap = {p.vat: p for p in partners}

        # La retención SIEMPRE se crea a partir de una factura real — antes este
        # bloque creaba ve.wh.iva sueltas sin invoice_id (huérfanas, nunca debería
        # poder ocurrir). Se crea aquí también la factura, igual que en el resto
        # del generador de histórico.
        Move = self.env['account.move'].sudo()
        journal_sale = self.env['account.journal'].sudo().search(
            [('type', '=', 'sale'), ('company_id', '=', company_id)], limit=1)
        income_acct = self.env['account.account'].sudo().search(
            [('account_type', 'in', ('income', 'income_other')),
             ('company_ids', 'in', [company_id]), ('currency_id', '=', False)], limit=1)
        tax_16_sale = self.env['account.tax'].sudo().search(
            [('type_tax_use', '=', 'sale'), ('amount', '=', 16.0),
             ('amount_type', '=', 'percent'), ('company_id', '=', company_id)], limit=1)

        for vat, base, pct in demo_hist:
            partner = pmap.get(vat)
            if not partner:
                continue
            inv_id = False
            if journal_sale and income_acct:
                line_vals = {'name': f'Servicios — {h_r}', 'quantity': 1,
                             'price_unit': base, 'account_id': income_acct.id}
                if tax_16_sale:
                    line_vals['tax_ids'] = [(6, 0, [tax_16_sale.id])]
                inv = Move.create({
                    'move_type': 'out_invoice', 'partner_id': partner.id,
                    'invoice_date': h_fin, 'journal_id': journal_sale.id,
                    'company_id': company_id, 'invoice_line_ids': [(0, 0, line_vals)],
                })
                try:
                    inv.action_post()
                    inv_id = inv.id
                    # El post pudo auto-crear una ve.wh.iva "esperado" — se elimina,
                    # se recrea abajo directo en estado declarado con datos demo.
                    WhIva.search([('invoice_id', '=', inv.id)]).unlink()
                except Exception as e:
                    log.append(f'  ⚠ Factura histórica {vat}: {e}')
            else:
                log.append(f'  ⚠ Sin diario/cuenta de venta — retención histórica {vat} sin factura')
            WhIva.create({
                'partner_id':           partner.id,
                'company_id':           company_id,
                'periodo':              h_f,
                'invoice_id':           inv_id,
                'conciliacion_id':      hist.id,
                'monto_base':           base,
                'monto_iva':            round(base * 0.16, 2),
                'porcentaje_retencion': pct,
                'incluir_declaracion':  True,
                'state':                'confirmado',
                'estado_declaracion':   'declarado',
            })
        log.append(f'+ 3 retenciones demo creadas para {h_r}')

        self._asegurar_compra_excedente_hist(h_r, h_fin, company_id, log)

        decl = Declaracion._get_or_create_for_periodo(hist.id)
        if decl.estado != 'presentada':
            decl.write({'estado': 'presentada', 'fecha_declaracion': fields.Datetime.now()})
        hist.write({'estado': 'declarado'})
        log.append(f'✓ Período {h_r} declarado (historial demo)')

        # Crear comprobantes IVA Proveedores para el período histórico
        ppids = self._asegurar_proveedores([])
        self._crear_prov_hist_declarado(decl, ppids, h_year, h_month, log)

    def _crear_prov_hist_declarado(self, decl, ppids, year, month, log):
        """Crea 2 comprobantes ve.wh.iva.prov mínimos en estado declarado para el historial demo."""
        WhIvaProv = self.env['ve.wh.iva.prov'].sudo()
        now     = fields.Datetime.now()
        uid     = self.env.user.id
        datos = [
            ('J-88000001-0', 'NC-HIST-001', 200_000),
            ('J-88000002-0', 'NC-HIST-002', 150_000),
        ]
        creados = 0
        for vat, ctrl, base_16 in datos:
            partner = ppids.get(vat)
            if not partner:
                continue
            try:
                WhIvaProv.create({
                    'partner_id':           partner.id,
                    'declaracion_iva_id':   decl.id,
                    'nro_control':          ctrl,
                    'fecha':                f'{year:04d}-{month:02d}-20',
                    'monto_base_16':        base_16,
                    'porcentaje_retencion': 75.0,
                    'state':                'declarado',
                    'fecha_envio':          now,
                    'enviado_por_id':       uid,
                    'fecha_declaracion':    now,
                    'declarado_por_id':     uid,
                })
                creados += 1
            except Exception as e:
                log.append(f'  ⚠ IVA Prov hist {vat}: {e}')
        if creados:
            decl.write({
                'estado_prov':            'declarado',
                'declarado_prov_por_id':  uid,
                'fecha_declaracion_prov': now,
                'nro_declaracion_prov':   'DEMO-HIST-PROV',
            })
            log.append(f'+ {creados} comprobantes IVA Prov. historial creados y declarados')
