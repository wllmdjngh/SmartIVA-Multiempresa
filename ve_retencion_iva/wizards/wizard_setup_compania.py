from odoo import fields, models


class VeWizardSetupCompania(models.TransientModel):
    _name = 've.wizard.setup.compania'
    _description = 'Configuración Inicial — Compañía Nueva (plan de cuentas, diarios e impuestos mínimos)'

    resultado = fields.Text(string='Resultado', readonly=True)
    ejecutado = fields.Boolean(default=False)
    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True,
        default=lambda self: self.env.company,
        help='Compañía a configurar — cambiar antes de ejecutar si no es la activa.',
    )
    ocultar_menus_demo = fields.Boolean(
        string='Ocultar menús de datos de prueba',
        default=True,
        help='Desactiva "Reiniciar Demo" y "Reiniciar Período Demo — Piloto '
             'Conecta" (Utilitarios) — herramientas de datos SINTÉTICOS '
             'pensadas para el sandbox de QA, que no aplican a un despacho '
             'real. Afecta a TODA la base (los menús no son por compañía), '
             'así que solo debe marcarse en un proyecto Odoo.sh dedicado a '
             'un piloto/cliente real — desmarcar si se corre este wizard '
             'dentro de la misma base de QA.',
    )
    usuario_nombre = fields.Char(
        string='Nombre del usuario cliente',
        help='Si se completa junto con el correo, el wizard crea el usuario '
             'del cliente en esta compañía. Dejar en blanco para no crear '
             'ningún usuario (ej. si ya existe o se hará a mano después).',
    )
    usuario_email = fields.Char(string='Correo del usuario cliente')
    usuario_rol = fields.Selection([
        ('gestor', 'Gestor (acceso completo: confirmar, conciliar, declarar)'),
        ('usuario', 'Usuario (solo lectura y carga de comprobantes)'),
    ], string='Rol', default='gestor')

    def action_configurar(self):
        """Crea el plan de cuentas/diarios/impuestos mínimos que SmartIVA
        necesita para operar, sin depender de instalar l10n_ve (decisión de
        arquitectura del módulo — ver manifest, depends solo de account/
        mail/iap). Pensado para una compañía real nueva (a diferencia de
        ve.reset.piloto.wizard, que genera además data sintética de
        prueba). Idempotente: si ya existe una cuenta/diario/impuesto que
        cumple el criterio, lo reutiliza y lo reporta, no lo duplica.

        Extraído y generalizado 2026-07-28 desde
        wizard_reset_piloto.py::_asegurar_contabilidad_basica (mismo
        patrón, ya probado en la compañía PILOTO de QA) — acá usa nombres/
        códigos neutros en vez de "Piloto", y los 2 códigos de IVA
        documentados en README.md (1151004/2172003) para que sirvan a la
        vez de cuenta de impuesto real Y de cuenta de retención
        (ve_cuenta_iva_retenido_cobrar_id/ve_cuenta_iva_por_pagar_id) —
        mismo par de cuentas para ambos usos, como ya asume
        post_init_hook."""
        self.ensure_one()
        company = self.company_id
        log = self._configurar(company)
        # Cada paso opcional corre en su propio savepoint — un error en
        # UNO (ej. crear el usuario) no debe revertir el plan de cuentas/
        # diarios/impuestos ya creados en _configurar(). Bug real
        # 2026-07-30: 'groups_id' ya no existe en Odoo 19 (renombrado a
        # 'group_ids'), y al no estar aislado el error tumbó TODA la
        # transacción del wizard, perdiendo también la contabilidad recién
        # armada — corregido el nombre de campo, y este aislamiento evita
        # que un fallo similar en el futuro vuelva a costar tan caro.
        log.append('')
        try:
            with self.env.cr.savepoint():
                log += self._actualizar_tasa_bcv(company)
        except Exception as exc:
            log.append(f'⚠ Tasa BCV: error inesperado, paso omitido ({exc})')
        if self.usuario_email:
            log.append('')
            try:
                with self.env.cr.savepoint():
                    log += self._crear_usuario_cliente(company)
            except Exception as exc:
                log.append(f'⚠ Usuario del cliente: error inesperado, paso omitido ({exc})')
        if self.ocultar_menus_demo:
            log.append('')
            try:
                with self.env.cr.savepoint():
                    log += self._ocultar_menus_demo()
            except Exception as exc:
                log.append(f'⚠ Ocultar menús: error inesperado, paso omitido ({exc})')
        self.resultado = '\n'.join(log)
        self.ejecutado = True
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name,
            'res_id': self.id, 'view_mode': 'form', 'target': 'new',
        }

    def _actualizar_tasa_bcv(self, company):
        """Corre la actualización manual de tasas BCV (ve_bcv_rates) para
        que la compañía nueva arranque con la tasa VES del día ya cargada
        — antes había que correr el botón aparte (Bloque 6 del runbook).
        Si ve_bcv_rates no está instalado, se omite sin error (el módulo
        no depende de él, es opcional)."""
        Currency = self.env['res.currency'].sudo()
        if not hasattr(Currency, 'action_update_bcv_rates'):
            return ['= Tasa BCV: módulo ve_bcv_rates no instalado, paso omitido']
        try:
            Currency.action_update_bcv_rates(manual=True)
            return ['+ Tasa BCV actualizada (ve_bcv_rates)']
        except Exception as exc:
            return [f'⚠ Tasa BCV: no se pudo actualizar ahora ({exc}) — '
                    'reintentar con el botón manual más tarde (puede ser que '
                    'el portal del BCV esté temporalmente no disponible).']

    def _crear_usuario_cliente(self, company):
        """Crea el usuario del cliente en esta compañía, sin invitación
        automática por correo (se invita a mano cuando el entorno ya esté
        validado) — decisión explícita 2026-07-30. Idempotente: si ya
        existe un usuario con ese login/correo, solo ajusta compañía y
        grupo en vez de crear uno nuevo."""
        Users = self.env['res.users'].sudo()
        grupo_xml_id = (
            've_retencion_iva.group_ret_iva_gestor' if self.usuario_rol == 'gestor'
            else 've_retencion_iva.group_ret_iva_usuario'
        )
        grupo = self.env.ref(grupo_xml_id)
        usuario = Users.search([('login', '=', self.usuario_email)], limit=1)
        if usuario:
            usuario.write({
                'company_id': company.id,
                'company_ids': [(4, company.id)],
                'group_ids': [(4, grupo.id)],
            })
            return [f'= Usuario "{usuario.name}" ya existía — compañía/rol actualizados']
        usuario = Users.create({
            'name': self.usuario_nombre or self.usuario_email,
            'login': self.usuario_email,
            'email': self.usuario_email,
            'company_id': company.id,
            'company_ids': [(6, 0, [company.id])],
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id, grupo.id,
            ])],
        })
        return [
            f'+ Usuario "{usuario.name}" ({self.usuario_email}) creado, '
            f'rol {"Gestor" if self.usuario_rol == "gestor" else "Usuario"} '
            '— SIN invitación enviada, invitar a mano cuando esté listo '
            '(Ajustes → Usuarios → seleccionar → "Invitar").'
        ]

    def _ocultar_menus_demo(self):
        """Desactiva los menús de datos de prueba (QA-only) que no
        aplican a un piloto/cliente real — pedido explícito 2026-07-29,
        para no repetir el paso manual (Ajustes > Técnico > Menús) en
        cada proyecto nuevo. Los menús no son scoped por compañía, así
        que esto oculta la opción para TODA la base — correcto en un
        proyecto Odoo.sh dedicado a un solo piloto, no en QA."""
        log = []
        menu_ids = [
            've_retencion_iva.DJCS_menu_iva_clientes_demo',
            've_retencion_iva.DJCS_menu_iva_clientes_piloto',
        ]
        for xml_id in menu_ids:
            menu = self.env.ref(xml_id, raise_if_not_found=False)
            if menu and menu.active:
                menu.sudo().active = False
                log.append(f'+ Menú "{menu.name}" desactivado (datos de prueba QA-only)')
            elif menu:
                log.append(f'= Menú "{menu.name}" ya estaba desactivado')
        return log

    def _asegurar_cuenta(self, company, code, name, account_type, reconcile=False):
        """Busca la cuenta por código sin filtrar por `company_ids` antes
        de crearla. Motivo: buscar solo scoped a `company.id` (como hacía
        este wizard antes) puede dejar pasar por alto una cuenta con el
        mismo código que ya exista en la base para OTRA compañía —
        create() choca entonces con la validación de código duplicado
        ("Los códigos de cuenta deben ser únicos"), visto en vivo
        2026-07-29 con el código 1151004. No confirmado si Odoo exige
        código único siempre a nivel de toda la base o si fue una
        circunstancia puntual de este caso (la compañía se recreó después
        con la Localización Fiscal fijada desde el inicio y no volvió a
        pasar) — de cualquier forma, buscar antes de crear y reutilizar/
        extender `company_ids` si ya existe es más seguro que asumir que
        el código está libre."""
        Account = self.env['account.account'].sudo()
        account = Account.search([('code', '=', code)], limit=1)
        if account:
            if company.id not in account.company_ids.ids:
                account.company_ids = [(4, company.id)]
                # 'code' es company-dependent en Odoo 19 — agregar la
                # compañía a company_ids no le fija un código en ESE
                # contexto, queda en blanco ahí y Odoo lo rechaza
                # ("El código debe estar configurado para todas las
                # empresas a las que pertenece esta cuenta"). Bug real
                # encontrado 2026-07-31 (proyecto Cementos).
                account.with_company(company).code = code
                return account, f'= Cuenta "{name}" ({code}) ya existía en otra compañía — agregada a esta'
            return account, f'= Cuenta "{name}" ({code}) ya existía'
        account = Account.create({
            'name': name, 'code': code, 'account_type': account_type,
            'reconcile': reconcile,
            'company_ids': [(6, 0, [company.id])],
        })
        return account, f'+ Cuenta "{name}" creada ({code})'

    def _configurar(self, company):
        # 'code' de account.account es company-dependent en Odoo 19 — sin
        # operar explícitamente en el contexto de LA COMPAÑÍA QUE SE ESTÁ
        # CONFIGURANDO (no la compañía activa de la sesión del usuario, que
        # puede ser otra — el propio campo company_id de este wizard avisa
        # "cambiar antes de ejecutar si no es la activa"), hasta una cuenta
        # recién creada podía terminar con su código escrito en el contexto
        # equivocado, dejando el de esta compañía en blanco. Bug real
        # encontrado 2026-07-31 (proyecto Cementos, persistía incluso tras
        # el primer fix que solo cubría reutilizar una cuenta existente).
        self = self.with_company(company)
        log = []
        Journal = self.env['account.journal'].sudo()
        Tax = self.env['account.tax'].sudo()

        # País — SIEMPRE Venezuela, explícito (el módulo es específico para
        # VE, no tiene sentido depender de que la compañía lo haya
        # completado bien). Bug real 2026-07-28: account.tax exige
        # country_id, y este wizard solo lo llenaba SI company.country_id
        # ya estaba seteado — si el campo País de la compañía había
        # quedado vacío (fácil de pasar por alto al crearla a mano), la
        # creación del impuesto fallaba con "Falta el valor requerido para
        # el campo 'País'".
        country_ve = self.env['res.country'].sudo().search([('code', '=', 'VE')], limit=1)
        if country_ve and not company.country_id:
            company.country_id = country_ve.id
            log.append('+ País de la compañía completado (Venezuela)')

        # ── Cuentas base ─────────────────────────────────────────────────
        income, msg = self._asegurar_cuenta(company, '4111001', 'Ingresos', 'income')
        log.append(msg)

        expense, msg = self._asegurar_cuenta(company, '5111001', 'Gastos', 'expense')
        log.append(msg)

        # Sin esto, la línea de "payment_term" que Odoo arma automáticamente
        # al postear la factura (el saldo con el cliente/proveedor) queda
        # sin account_id y la factura no se puede postear — mismo hallazgo
        # real de wizard_reset_piloto.py (QA, 2026-07-20).
        receivable, msg = self._asegurar_cuenta(
            company, '1131001', 'Cuentas por Cobrar', 'asset_receivable', reconcile=True)
        log.append(msg)

        payable, msg = self._asegurar_cuenta(
            company, '2111001', 'Cuentas por Pagar', 'liability_payable', reconcile=True)
        log.append(msg)

        # ── Diarios ──────────────────────────────────────────────────────
        if not Journal.search([('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1):
            Journal.create({'name': 'Ventas', 'type': 'sale', 'code': 'VTA', 'company_id': company.id})
            log.append('+ Diario de Ventas creado')
        else:
            log.append('= Diario de Ventas ya existía')

        if not Journal.search([('type', '=', 'purchase'), ('company_id', '=', company.id)], limit=1):
            Journal.create({'name': 'Compras', 'type': 'purchase', 'code': 'CMP', 'company_id': company.id})
            log.append('+ Diario de Compras creado')
        else:
            log.append('= Diario de Compras ya existía')

        # Lo exige action_confirmar() de ve.wh.iva para postear el asiento
        # "IVA Retenido por Cobrar / IVA por Pagar" (_crear_asiento_contable).
        if not Journal.search([('type', '=', 'general'), ('company_id', '=', company.id)], limit=1):
            Journal.create({'name': 'Miscelánea', 'type': 'general', 'code': 'MISC', 'company_id': company.id})
            log.append('+ Diario Miscelánea creado (asiento de retención)')
        else:
            log.append('= Diario Miscelánea ya existía')

        # Sin esto, account.payment.register (registrar pagos, ej. desde
        # CONECTA-14 con EstadoPago=Pagada) no tiene dónde postear el pago.
        banco = Journal.search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
        if not banco:
            banco = Journal.create({'name': 'Banco', 'type': 'bank', 'code': 'BNK', 'company_id': company.id})
            log.append('+ Diario de Banco creado (para registrar pagos)')
        else:
            log.append('= Diario de Banco ya existía')

        # Cuenta Transitoria del diario Banco (`suspense_account_id`) -- no
        # es la causa del error de pago (ver comentario de
        # transfer_account_id más abajo, causa real), pero sigue siendo
        # buena práctica para la conciliación bancaria nativa. Idempotente.
        transitoria, msg = self._asegurar_cuenta(
            company, '1132001', 'Cuenta Transitoria', 'asset_current', reconcile=True)
        log.append(msg)

        if not banco.suspense_account_id:
            banco.suspense_account_id = transitoria.id
            log.append('+ "Cuenta Transitoria" (Diario Banco) → 1132001')
        else:
            log.append('= "Cuenta Transitoria" ya estaba configurada')

        # Causa real del "No se encontró ninguna cuenta pendiente para
        # realizar el pago" -- encontrada 2026-08-29 (INGREDIA) leyendo el
        # código fuente real de Odoo (account/models/account_payment.py::
        # _get_outstanding_account) tras 2 intentos fallidos adivinando
        # (`outstanding_receipts_account_id` de Odoo 17, después
        # `suspense_account_id` del diario -- ninguno de los 2 es lo que ese
        # método usa). El método real busca la cuenta vía la plantilla de
        # plan de cuentas (`chart_template.ref('account_journal_payment_
        # debit/credit_account_id')`), y si la compañía no tiene
        # `chart_template` instalado (nuestro caso -- el módulo no depende
        # de l10n_ve a propósito, ver manifest), cae a
        # `company.transfer_account_id` ("Cuenta de Transferencia") -- que
        # tampoco se auto-asigna sin chart_template. Sin NINGUNA de las 2,
        # revienta con ese error, sin importar qué tan bien configurado
        # esté el diario Banco (de ahí que arreglar suspense_account_id no
        # alcanzara). Confirmado por RPC en vivo: asignar transfer_account_id
        # resolvió el registro de pago en la misma factura que fallaba.
        if not company.transfer_account_id:
            company.transfer_account_id = transitoria.id
            log.append('+ "Cuenta de Transferencia" (Compañía) → 1132001')
        else:
            log.append('= "Cuenta de Transferencia" ya estaba configurada')

        # ── Cuentas IVA — mismo par (1151004/2172003) que ya documenta
        #    README.md y busca post_init_hook al instalar el módulo. Sirven
        #    a la vez de cuenta de reparto del impuesto real (account.tax) y
        #    de cuenta de retención (ve_cuenta_iva_retenido_cobrar_id/
        #    ve_cuenta_iva_por_pagar_id) — no se separan en 2 pares como
        #    hacía la versión "Piloto" (esa sí aislaba todo a propósito por
        #    ser una compañía sandbox desechable). ────────────────────────
        iva_credito, msg = self._asegurar_cuenta(
            company, '1151004', 'I.V.A. Crédito Fiscal', 'asset_current')
        log.append(msg)

        iva_debito, msg = self._asegurar_cuenta(
            company, '2172003', 'I.V.A. Débito Fiscal', 'liability_current')
        log.append(msg)

        if not company.ve_cuenta_iva_retenido_cobrar_id:
            company.ve_cuenta_iva_retenido_cobrar_id = iva_credito.id
            log.append('+ "IVA Retenido por Cobrar" (Configuración IVA) → 1151004')
        else:
            log.append('= "IVA Retenido por Cobrar" ya estaba configurado')

        if not company.ve_cuenta_iva_por_pagar_id:
            company.ve_cuenta_iva_por_pagar_id = iva_debito.id
            log.append('+ "IVA por Pagar" (Configuración IVA) → 2172003')
        else:
            log.append('= "IVA por Pagar" ya estaba configurado')

        # ── Impuestos 16%/8% venta y compra ─────────────────────────────
        # Bug real 2026-07-28: el fallback "cualquier grupo de impuestos en
        # toda la base" (search([], limit=1)) podía agarrar un grupo de
        # OTRO país — Odoo exige que el tax_group tenga el mismo country_id
        # que los impuestos que lo usan. Ahora se busca/crea scoped por
        # compañía Y país (VE), sin fallback genérico.
        country_id_ve = country_ve.id if country_ve else False
        tax_group = self.env['account.tax.group'].sudo().search([
            ('company_id', '=', company.id),
            ('country_id', '=', country_id_ve),
        ], limit=1)
        if not tax_group:
            tax_group = self.env['account.tax.group'].sudo().create({
                'name': 'IVA', 'company_id': company.id,
                'country_id': country_id_ve,
            })
            log.append('+ Grupo de impuestos "IVA" creado')
        else:
            log.append('= Grupo de impuestos "IVA" ya existía')

        cuentas_tax = {'sale': iva_debito, 'purchase': iva_credito}
        for tipo_uso, label in (('sale', 'Venta'), ('purchase', 'Compra')):
            for alicuota in (16.0, 8.0):
                existe = Tax.search([
                    ('type_tax_use', '=', tipo_uso), ('amount', '=', alicuota),
                    ('amount_type', '=', 'percent'), ('company_id', '=', company.id),
                ], limit=1)
                if existe:
                    log.append(f'= Impuesto {label} {alicuota:.0f}% ya existía')
                    continue
                cuenta_tax = cuentas_tax[tipo_uso]
                rep_lines = [
                    (0, 0, {'factor_percent': 100, 'repartition_type': 'base'}),
                    (0, 0, {
                        'factor_percent': 100, 'repartition_type': 'tax',
                        'account_id': cuenta_tax.id,
                    }),
                ]
                tax_vals = {
                    'name': f'IVA {alicuota:.0f}% {label}',
                    'amount': alicuota, 'amount_type': 'percent',
                    'type_tax_use': tipo_uso, 'company_id': company.id,
                    'tax_group_id': tax_group.id,
                    'invoice_repartition_line_ids': rep_lines,
                    'refund_repartition_line_ids': rep_lines,
                }
                if country_ve:
                    tax_vals['country_id'] = country_ve.id
                Tax.create(tax_vals)
                log.append(f'+ Impuesto "IVA {alicuota:.0f}% {label}" creado')

        log.append('')
        log.append(f'Listo — compañía "{company.name}" configurada.')
        log.append(
            'Pendiente aparte (no lo hace este wizard): diarios/cuentas '
            'reales del cliente si difieren de estos códigos genéricos, '
            'idioma, moneda VES, y los datos propios de la compañía '
            '(RIF, domicilio, condición de contribuyente).'
        )
        return log
