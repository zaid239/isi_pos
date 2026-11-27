import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Max, Sum, F, Q
from django.db import transaction
from .models import Customer, Country, City, Company, InvoiceMst, InvoiceDet, InvoicePaymentMst, InvoicePaymentDet, TaxSetup
from karar.models import Item, PaymentTerm, Director

def require_sale_session(request):
    from django.http import JsonResponse
    if not request.session.get('usrid'):
        return JsonResponse({'success': False, 'message': 'Not authenticated. Please login.'}, status=401)
    return None

@csrf_exempt
def sale_lookup_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    if request.method == 'GET':
        try:
            countries = list(Country.objects.all().values())
            cities = list(City.objects.all().values())
            companies = list(Company.objects.all().values())
            customers = list(Customer.objects.all().values())
            directors = list(Director.objects.all().values())
            payment_terms = list(PaymentTerm.objects.all().values())
            from django.db import connection
            items = []
            if request.GET.get('skip_items') != '1':
                with connection.cursor() as cursor:
                    try:
                        cursor.cursor.cursor.arraysize = 5000
                    except Exception:
                        pass
                    cursor.execute("""
                        WITH ledger AS (
                            SELECT item_code, SUM(NVL(in_qty, 0) - NVL(out_qty, 0)) AS bal
                            FROM item_ledger
                            WHERE NVL(is_deleted, 'N') = 'N'
                            GROUP BY item_code
                        )
                        SELECT i.item_code, i.item_name, i.item_name2, i.unit, i.stock, NVL(l.bal, 0)
                        FROM item i
                        LEFT JOIN ledger l ON i.item_code = l.item_code
                    """)
                    for r in cursor.fetchall():
                        items.append({
                            'item_code': r[0],
                            'item_name': r[1],
                            'item_name2': r[2],
                            'unit': r[3],
                            'stock': r[4],
                            'ledger_stock': float(r[5] or 0.0)
                        })
            
            from karar.models import GlCoa
            bank_accounts = list(GlCoa.objects.filter(parent_code_id='101', is_active=1).values('acc_code', 'acc_name'))
            cash_accounts = list(GlCoa.objects.filter(parent_code_id='105', is_active=1).values('acc_code', 'acc_name'))
            
            from django.db.models import Q
            invoiced_dc_tcodes = InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', ref_tcode__isnull=False).values_list('ref_tcode', flat=True)
            pending_dcs = list(InvoiceMst.objects.exclude(is_deleted=1).filter(
                dcode__startswith='1', 
                cmp_code_id=1
            ).exclude(
                tcode__in=invoiced_dc_tcodes
            ).exclude(
                dc_flag='Y'
            ).filter(
                Q(invoice_no__isnull=True) | Q(invoice_no='')
            ).values('tcode', 'docno', 'invoice_no'))
            
            try:
                taxes = list(TaxSetup.objects.all().values())
            except Exception:
                taxes = []

            return JsonResponse({
                'success': True,
                'countries': countries,
                'cities': cities,
                'companies': companies,
                'customers': customers,
                'directors': directors,
                'payment_terms': payment_terms,
                'items': items,
                'bank_accounts': bank_accounts,
                'cash_accounts': cash_accounts,
                'pending_dcs': pending_dcs,
                'taxes': taxes,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=500)

@csrf_exempt
def customer_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    if request.method == 'GET':
        try:
            customers = list(Customer.objects.all().values())
            return JsonResponse({'success': True, 'customers': customers})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            # Generate new cstcode if not provided
            cstcode = data.get('cstcode')
            if not cstcode or str(cstcode).strip() == '':
                max_code = Customer.objects.aggregate(Max('cstcode'))['cstcode__max']
                cstcode = (max_code or 0) + 1

            acc_code = data.get('acc_code', '10201')
            if acc_code == '210101':
                return JsonResponse({'success': False, 'message': 'Account 210101 is a control account and cannot be assigned to customers.'})

            cust_type_val = data.get('cust_type') or data.get('custtype')
            customer = Customer.objects.create(
                cstcode=cstcode,
                name=data.get('name'),
                short_name=data.get('short_name'),
                country_code=data.get('country_code'),
                city_code=data.get('city_code'),
                address=data.get('address'),
                phone_number=data.get('phone_number'),
                ntn=data.get('ntn'),
                srb=data.get('srb'),
                cust_type=cust_type_val,
                custtype=cust_type_val,
                status=data.get('status', 'E'),
                contact_person=data.get('contact_person'),
                mobile=data.get('mobile'),
                email=data.get('email'),
                acc_code=acc_code,
                tax_category=data.get('tax_category', 'default'),
                entby='admin',
            )
            return JsonResponse({'success': True, 'message': 'Customer created successfully', 'cstcode': customer.cstcode})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'PUT':
        try:
            data = json.loads(request.body)
            cstcode = data.get('cstcode')
            customer = Customer.objects.get(cstcode=cstcode)
            
            customer.name = data.get('name', customer.name)
            customer.short_name = data.get('short_name', customer.short_name)
            customer.country_code = data.get('country_code', customer.country_code)
            customer.city_code = data.get('city_code', customer.city_code)
            customer.address = data.get('address', customer.address)
            customer.phone_number = data.get('phone_number', customer.phone_number)
            customer.ntn = data.get('ntn', customer.ntn)
            customer.srb = data.get('srb', customer.srb)
            if 'cust_type' in data or 'custtype' in data:
                c_val = data.get('cust_type') if 'cust_type' in data else data.get('custtype')
                customer.cust_type = c_val
                customer.custtype = c_val
            customer.status = data.get('status', customer.status)
            customer.contact_person = data.get('contact_person', customer.contact_person)
            customer.mobile = data.get('mobile', customer.mobile)
            customer.email = data.get('email', customer.email)
            customer.tax_category = data.get('tax_category', customer.tax_category)
            new_acc_code = data.get('acc_code')
            if new_acc_code == '210101':
                return JsonResponse({'success': False, 'message': 'Account 210101 is a control account and cannot be assigned to customers.'})
            if new_acc_code:
                customer.acc_code = new_acc_code
            customer.modiby = 'admin'
            
            customer.save()
            return JsonResponse({'success': True, 'message': 'Customer updated successfully'})
        except Customer.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Customer not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'DELETE':
        try:
            data = json.loads(request.body)
            cstcode = data.get('cstcode')
            customer = Customer.objects.get(cstcode=cstcode)
            customer.delete()
            return JsonResponse({'success': True, 'message': 'Customer deleted successfully'})
        except Customer.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Customer not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

@csrf_exempt
def generate_invoice_no_api(request):
    """
    Converted from Oracle PL/SQL:
    Generates a formatted invoice number (e.g. 2526-00001) based on:
      - The invoice's inv_date and cmp_code (from tcode in InvoiceMst, or passed directly)
      - The fiscal year from FISCAL_YEAR table
      - The current sequence from SEQ_NOS table (auto-incremented atomically)
    
    Accepts POST with JSON body:
      { "tcode": <int> }              -- look up inv_date + cmp_code from existing invoice
      OR
      { "inv_date": "YYYY-MM-DD", "cmp_code": <int> }  -- provide directly (for new invoices)
    """
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error

    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Invalid JSON body.'}, status=400)

    from django.db import connection

    try:
        # ---- STEP 1: Get inv_date and cmp_code ----
        tcode = data.get('tcode')
        inv_date_str = data.get('inv_date')
        cmp_code = data.get('cmp_code')
        ref_tcode = data.get('ref_tcode')
        challan_no = data.get('challan_no')

        if ref_tcode:
            existing_inv = InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', ref_tcode=ref_tcode).first()
            if existing_inv:
                return JsonResponse({
                    'success': False,
                    'message': f"Invoice (#{existing_inv.invoice_no or existing_inv.docno}) has already been generated for this Delivery Challan."
                }, status=400)
        elif challan_no and cmp_code and not tcode:
            existing_inv = InvoiceMst.objects.exclude(is_deleted=1).filter(
                dcode='2', challan_no=str(challan_no).strip(), cmp_code_id=cmp_code
            ).first()
            if existing_inv:
                return JsonResponse({
                    'success': False,
                    'message': f"Invoice (#{existing_inv.invoice_no or existing_inv.docno}) has already been generated for Delivery Challan #{challan_no}."
                }, status=400)

        if tcode:
            # Look up from existing invoice record
            try:
                master = InvoiceMst.objects.get(tcode=tcode)
                inv_date = master.inv_date
                cmp_code = master.cmp_code_id
            except InvoiceMst.DoesNotExist:
                return JsonResponse({'success': False, 'message': 'Invoice details not found for the given TCODE!'}, status=404)
            except InvoiceMst.MultipleObjectsReturned:
                return JsonResponse({'success': False, 'message': 'Multiple invoices found for this TCODE!'}, status=400)
        elif inv_date_str and cmp_code:
            from datetime import date as dt_date
            try:
                inv_date = dt_date.fromisoformat(inv_date_str)
                cmp_code = int(cmp_code)
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'message': 'Invalid inv_date or cmp_code format.'}, status=400)
        else:
            return JsonResponse({'success': False, 'message': 'Provide either tcode OR (inv_date + cmp_code).'}, status=400)

        # ---- STEP 2: Fetch fiscal year ----
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT FCODE FROM FISCAL_YEAR WHERE :inv_date BETWEEN FINSTART AND FINEND",
                {'inv_date': inv_date}
            )
            row = cursor.fetchone()
            if not row:
                return JsonResponse({'success': False, 'message': 'Fiscal year not defined for the invoice date!'}, status=400)
            fcode = row[0]

        # ---- STEP 3: Atomically increment the sequence ----
        with transaction.atomic():
            with connection.cursor() as cursor:
                # Try UPDATE first (atomic increment)
                cursor.execute(
                    """
                    UPDATE SEQ_NOS
                    SET SEQNO = NVL(SEQNO, 0) + 1
                    WHERE FCODE = :fcode AND CMP_CODE = :cmp_code
                    """,
                    {'fcode': fcode, 'cmp_code': cmp_code}
                )
                rows_updated = cursor.rowcount

                if rows_updated == 0:
                    # First time for this fiscal year + company — insert row
                    cursor.execute(
                        """
                        INSERT INTO SEQ_NOS (SEQTYPE, SEQNO, FCODE, CMP_CODE)
                        VALUES ('INV', 1, :fcode, :cmp_code)
                        """,
                        {'fcode': fcode, 'cmp_code': cmp_code}
                    )
                    n = 1
                else:
                    # Read the newly updated value
                    cursor.execute(
                        "SELECT SEQNO FROM SEQ_NOS WHERE FCODE = :fcode AND CMP_CODE = :cmp_code",
                        {'fcode': fcode, 'cmp_code': cmp_code}
                    )
                    n = cursor.fetchone()[0]

        # ---- STEP 4: Format the manual number ----
        # e.g. fcode = "2025-2026" => strip "20" and "-" => "2526"
        fcode_short = fcode.replace('20', '').replace('-', '')
        manual_no = f"{fcode_short}-{str(n).zfill(5)}"

        return JsonResponse({'success': True, 'manual_no': manual_no, 'fcode': fcode, 'seqno': n})

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@csrf_exempt
def invoice_api(request):

    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    if request.method == 'GET':
        try:
            tcode_str = request.GET.get('tcode')
            if tcode_str:
                tcode = int(tcode_str)
                master = InvoiceMst.objects.get(tcode=tcode)
                
                # Fetch DC Date and DC Remarks from matching Delivery Challan
                dc_date = None
                dc_remarks = ''
                if (master.dcode or '').strip() != '1' and master.challan_no:
                    dc = InvoiceMst.objects.filter(dcode='1', challan_no=master.challan_no, cstcode=master.cstcode, cmp_code=master.cmp_code).first()
                    if dc:
                        dc_date = dc.inv_date.isoformat() if dc.inv_date else None
                        dc_remarks = dc.remarks or ''
                elif (master.dcode or '').strip() == '1':
                    dc_date = master.inv_date.isoformat() if master.inv_date else None
                    dc_remarks = master.remarks or ''

                master_data = {
                    'tcode': master.tcode,
                    'docno': master.docno,
                    'dcode': master.dcode.strip() if master.dcode else '1',
                    'inv_date': master.inv_date.isoformat() if master.inv_date else None,
                    'cstcode': master.cstcode_id,
                    'invoice_no': master.invoice_no,
                    'challan_no': master.challan_no,
                    'po_no': master.po_no,
                    'cmp_code': master.cmp_code_id,
                    'opening_flag': master.opening_flag,
                    'gstbase_flag': master.gstbase_flag,
                    'dc_flag': getattr(master, 'dc_flag', 'N') or 'N',
                    'pcode': master.pcode_id,
                    'dir_code': master.dir_code,
                    'remarks': master.remarks,
                    'delivery_charges': float(getattr(master, 'delivery_charges', 0) or 0),
                    'discount': float(getattr(master, 'discount', 0) or 0),
                    'invoice_date': master.invoice_date.isoformat() if master.invoice_date else None,
                    'invoice_remarks': master.invoice_remarks,
                    'invoice_generated_date': master.invoice_generated_date.isoformat() if master.invoice_generated_date else None,
                    'ref_tcode': master.ref_tcode,
                    'is_invoiced': InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', ref_tcode=master.tcode).exists() if (master.dcode or '').strip() == '1' else False,
                    'dc_date': dc_date,
                    'dc_remarks': dc_remarks,
                }
                
                details = list(InvoiceDet.objects.filter(tcode=tcode).values(
                    'item_code', 'item_code__item_name', 'item_code__item_name2', 'qty', 'tmp_qty', 'rate', 'gst', 'dremarks'
                ))
                
                return JsonResponse({'success': True, 'master': master_data, 'details': details})
            else:
                # List all invoices with filters
                start_date = request.GET.get('start_date')
                end_date = request.GET.get('end_date')
                if not start_date or not end_date:
                    return JsonResponse({'success': True, 'invoices': []})
                
                cmp_code = request.GET.get('cmp_code')
                cstcode = request.GET.get('cstcode')
                po_no = request.GET.get('po_no')
                invoice_no = request.GET.get('invoice_no')
                challan_no = request.GET.get('challan_no')
                
                dcode = request.GET.get('dcode')
                query = InvoiceMst.objects.exclude(is_deleted=1)
                if dcode:
                    from django.db.models import Q
                    if dcode.strip() == '1':
                        # All Delivery Challans (dcode='1')
                        query = query.filter(dcode='1')
                    elif dcode.strip() == '2':
                        # All Sales Invoices (dcode='2')
                        query = query.filter(dcode='2')
                    elif dcode.strip() == '7':
                        # Negative Adjustments (dcode='7')
                        query = query.filter(dcode='7')
                    else:
                        query = query.filter(dcode__startswith=dcode)
                
                query = query.filter(inv_date__range=[start_date, end_date])
                if cmp_code:
                    query = query.filter(cmp_code_id=cmp_code)
                if cstcode:
                    query = query.filter(cstcode_id=cstcode)
                if po_no:
                    query = query.filter(po_no__icontains=po_no)
                if invoice_no:
                    from django.db.models import Q
                    query = query.filter(Q(invoice_no__icontains=invoice_no) | Q(docno__icontains=invoice_no))
                if challan_no:
                    query = query.filter(challan_no__icontains=challan_no)
                    
                from django.db.models.expressions import RawSQL
                query = query.annotate(amount=RawSQL("GET_INVOICE_AMOUNT(tcode)", []))
                invoices = list(query.order_by('-inv_date', '-tcode').values(
                    'tcode', 'docno', 'inv_date', 'invoice_no', 'po_no', 'challan_no', 
                    'cmp_code__cmp_name', 'cstcode__name', 'dcode', 'ref_tcode', 'amount',
                    'cmp_code_id', 'cstcode_id', 'remarks', 'is_posted', 'gl_srno',
                    'delivery_charges', 'discount', 'dc_flag'
                ))
                
                # Fetch all invoiced DC references to accurately flag is_invoiced
                invoiced_ref_tcodes = set(InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', ref_tcode__isnull=False).values_list('ref_tcode', flat=True))
                invoiced_challan_nos = set(InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', challan_no__isnull=False).values_list('challan_no', flat=True))

                # Bulk fetch matching Delivery Challans to get DC Date and DC Remarks
                challan_nos = [inv['challan_no'] for inv in invoices if inv['challan_no']]
                dc_map = {}
                if challan_nos:
                    dcs = InvoiceMst.objects.filter(dcode='1', challan_no__in=challan_nos)
                    for dc in dcs:
                        key = (dc.challan_no, dc.cmp_code_id, dc.cstcode_id)
                        dc_map[key] = {
                            'dc_date': dc.inv_date.isoformat() if dc.inv_date else None,
                            'dc_remarks': dc.remarks or ''
                        }
                
                for inv in invoices:
                    if (inv.get('dcode') or '').strip() == '1':
                        inv['dc_date'] = inv['inv_date'].isoformat() if inv['inv_date'] else None
                        inv['dc_remarks'] = inv['remarks'] or ''
                        is_inv = bool(inv.get('invoice_no') or inv['tcode'] in invoiced_ref_tcodes or (inv.get('challan_no') and inv['challan_no'] in invoiced_challan_nos))
                        inv['is_invoiced'] = is_inv
                    else:
                        key = (inv['challan_no'], inv['cmp_code_id'], inv['cstcode_id'])
                        dc_info = dc_map.get(key)
                        if dc_info:
                            inv['dc_date'] = dc_info['dc_date']
                            inv['dc_remarks'] = dc_info['dc_remarks']
                        else:
                            inv['dc_date'] = None
                            inv['dc_remarks'] = ''
                        inv['is_invoiced'] = True

                # DC filtering: invoiced_only, pending_only, or default
                pending_only_flag = request.GET.get('pending_only')
                invoiced_only_flag = request.GET.get('invoiced_only')
                if invoiced_only_flag == '1':
                    invoices = [inv for inv in invoices if inv.get('is_invoiced') and inv.get('dc_flag') != 'Y']
                elif pending_only_flag == '1':
                    invoices = [inv for inv in invoices if not inv.get('is_invoiced') and inv.get('dc_flag') != 'Y']
                elif (dcode and dcode.strip() == '1'):
                    invoices = [inv for inv in invoices if not inv.get('is_invoiced')]
                            
                return JsonResponse({'success': True, 'invoices': invoices})
        except InvoiceMst.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Invoice not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method in ['POST', 'PUT']:
        try:
            data = json.loads(request.body)
            master_data = data.get('master', {})
            details_data = data.get('details', [])

            dcode_val = (master_data.get('dcode') or '1').strip()
            po_no_val = (master_data.get('po_no') or '').strip()
            cstcode_val = master_data.get('cstcode')
            force_save = bool(data.get('force_save'))
            editing_tcode = master_data.get('tcode') if request.method == 'PUT' else None

            # Customer-scoped duplicate PO No check for Delivery Challans (dcode='1')
            if dcode_val == '1' and po_no_val and cstcode_val and not force_save:
                dup_qs = InvoiceMst.objects.filter(
                    dcode='1',
                    cstcode_id=cstcode_val,
                    po_no__iexact=po_no_val
                ).exclude(is_deleted=1)
                if editing_tcode:
                    dup_qs = dup_qs.exclude(tcode=editing_tcode)

                existing_dc = dup_qs.first()
                if existing_dc:
                    cst_name = existing_dc.cstcode.name if existing_dc.cstcode else ''
                    dc_identifier = existing_dc.challan_no or existing_dc.docno or str(existing_dc.tcode)
                    dc_date = existing_dc.inv_date.strftime('%d-%b-%Y') if existing_dc.inv_date else ''
                    return JsonResponse({
                        'success': False,
                        'po_duplicate': True,
                        'message': 'Po No Already Exists',
                        'existing_dc': {
                            'tcode': existing_dc.tcode,
                            'challan_no': dc_identifier,
                            'customer_name': cst_name,
                            'inv_date': dc_date
                        }
                    })
            # Prevent invoicing a Delivery Challan marked as ONLY DC
            if dcode_val == '2' and master_data.get('ref_tcode'):
                ref_dc = InvoiceMst.objects.filter(tcode=master_data.get('ref_tcode')).first()
                if ref_dc and getattr(ref_dc, 'dc_flag', 'N') == 'Y':
                    return JsonResponse({
                        'success': False,
                        'message': "This Delivery Challan is marked as 'ONLY DC' and cannot be invoiced."
                    })

            with transaction.atomic():
                if request.method == 'POST':
                    if dcode_val == '2':
                        ref_tcode_val = master_data.get('ref_tcode')
                        challan_val = (master_data.get('challan_no') or '').strip()
                        cmp_val = master_data.get('cmp_code')
                        from django.db import connection
                        with connection.cursor() as lock_cur:
                            if ref_tcode_val:
                                lock_cur.execute("SELECT tcode FROM invoice_mst WHERE tcode = %s FOR UPDATE", [int(ref_tcode_val)])
                            elif challan_val and challan_val != 'Auto':
                                lock_cur.execute("SELECT tcode FROM invoice_mst WHERE dcode = '1' AND challan_no = %s FOR UPDATE", [challan_val])

                        if ref_tcode_val:
                            dup_inv = InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', ref_tcode=ref_tcode_val).first()
                            if dup_inv:
                                return JsonResponse({
                                    'success': False,
                                    'message': f"An Invoice (#{dup_inv.invoice_no or dup_inv.docno}) has already been generated for this Delivery Challan."
                                }, status=400)
                        if challan_val and challan_val != 'Auto':
                            dup_ch_qs = InvoiceMst.objects.exclude(is_deleted=1).filter(dcode='2', challan_no=challan_val)
                            if cmp_val:
                                dup_ch_qs = dup_ch_qs.filter(cmp_code_id=cmp_val)
                            dup_ch = dup_ch_qs.first()
                            if dup_ch:
                                return JsonResponse({
                                    'success': False,
                                    'message': f"An Invoice (#{dup_ch.invoice_no or dup_ch.docno}) has already been generated for Challan #{challan_val}."
                                }, status=400)
                    max_tcode = InvoiceMst.objects.aggregate(Max('tcode'))['tcode__max']
                    tcode = (max_tcode or 0) + 1
                    
                    # Generate docno using stored procedure get_next_sales_trno
                    inv_date_str = master_data.get('inv_date')
                    docno = None
                    challan_no = master_data.get('challan_no')
                    if inv_date_str:
                        from django.db import connection
                        with connection.cursor() as django_cursor:
                            raw_cursor = django_cursor.cursor.cursor
                            # Get fiscal code
                            django_cursor.execute("SELECT GET_FISCALCODE(TO_DATE(%s, 'YYYY-MM-DD')) FROM DUAL", [inv_date_str])
                            fiscal_code = django_cursor.fetchone()[0]
                            
                            # Call procedure get_next_sales_trno
                            # P_SQNO is a VARCHAR2 out parameter
                            out_sqno = raw_cursor.var(str)
                            raw_cursor.callproc('GET_NEXT_SALES_TRNO', ['INV', fiscal_code, out_sqno])
                            docno = out_sqno.getvalue()

                            # Auto-generate challan_no if it is 'Auto' or empty
                            if not challan_no or challan_no == 'Auto':
                                django_cursor.execute("""
                                    SELECT nvl(max(TO_NUMBER(challan_no)), 0) + 1
                                    FROM INVOICE_MST
                                    WHERE DCODE = '1'
                                """)
                                next_challan = django_cursor.fetchone()[0]
                                if next_challan < 3000:
                                    next_challan = 3000
                                challan_no = str(int(next_challan))

                    master = InvoiceMst.objects.create(
                        tcode=tcode,
                        docno=docno,
                        dcode=master_data.get('dcode', '1'),
                        inv_date=master_data.get('inv_date'),
                        cstcode_id=master_data.get('cstcode'),
                        invoice_no=master_data.get('invoice_no'),
                        challan_no=challan_no,
                        po_no=master_data.get('po_no'),
                        cmp_code_id=master_data.get('cmp_code'),
                        opening_flag=master_data.get('opening_flag', 'N'),
                        gstbase_flag=master_data.get('gstbase_flag', 'N'),
                        dc_flag=master_data.get('dc_flag', 'N') or 'N',
                        pcode_id=master_data.get('pcode'),
                        dir_code=master_data.get('dir_code'),
                        remarks=master_data.get('remarks'),
                        delivery_charges=float(master_data.get('delivery_charges', 0) or 0),
                        discount=float(master_data.get('discount', 0) or 0),
                        ref_tcode=master_data.get('ref_tcode'),
                        ent_by=request.session.get('descr') or request.session.get('username') or master_data.get('ent_by') or 'system',
                    )
                else:
                    tcode = master_data.get('tcode')
                    master = InvoiceMst.objects.get(tcode=tcode)
                    sync_gl_dc = bool(data.get('sync_gl_dc') or data.get('allow_posted_edit'))
                    
                    # Prevent editing posted invoices UNLESS sync_gl_dc is enabled
                    if (master.is_posted == 'Y' or master.gl_srno) and not sync_gl_dc:
                        return JsonResponse({
                            'success': False,
                            'message': 'Permission Denied: Posted invoices cannot be modified.'
                        })
                        
                    # Prevent editing invoiced Delivery Challans UNLESS sync_gl_dc is enabled
                    if (master.dcode or '').strip() == '1' and not sync_gl_dc:
                        is_invoiced = InvoiceMst.objects.filter(
                            ref_tcode=master.tcode
                        ).exists()
                        if is_invoiced:
                            return JsonResponse({
                                'success': False,
                                'message': 'This Delivery Challan has already been invoiced and cannot be modified.'
                            })
                    
                    master.docno = master_data.get('docno', master.docno)
                    master.dcode = master_data.get('dcode', master.dcode)
                    master.inv_date = master_data.get('inv_date', master.inv_date)
                    master.cstcode_id = master_data.get('cstcode', master.cstcode_id)
                    master.invoice_no = master_data.get('invoice_no', master.invoice_no)
                    master.challan_no = master_data.get('challan_no', master.challan_no)
                    master.po_no = master_data.get('po_no', master.po_no)
                    master.cmp_code_id = master_data.get('cmp_code', master.cmp_code_id)
                    master.opening_flag = master_data.get('opening_flag', master.opening_flag)
                    master.gstbase_flag = master_data.get('gstbase_flag', master.gstbase_flag)
                    master.dc_flag = master_data.get('dc_flag', master.dc_flag or 'N')
                    master.pcode_id = master_data.get('pcode', master.pcode_id)
                    master.dir_code = master_data.get('dir_code', master.dir_code)
                    master.remarks = master_data.get('remarks', master.remarks)
                    master.delivery_charges = float(master_data.get('delivery_charges', 0) or 0)
                    master.discount = float(master_data.get('discount', 0) or 0)
                    master.ref_tcode = master_data.get('ref_tcode', master.ref_tcode)
                    master.modi_by = request.session.get('descr') or request.session.get('username') or master_data.get('modi_by') or 'system'
                    master.save()
                    
                    # Delete existing details
                    InvoiceDet.objects.filter(tcode=tcode).delete()

                # Insert Details
                det_objects = []
                for det in details_data:
                    det_objects.append(InvoiceDet(
                        tcode=master,
                        item_code_id=det.get('item_code'),
                        qty=det.get('qty', 0),
                        tmp_qty=det.get('tmp_qty', 0),
                        rate=det.get('rate', 0),
                        gst=det.get('gst', 0),
                        dremarks=det.get('dremarks', ''),
                    ))
                InvoiceDet.objects.bulk_create(det_objects)
                
                # Sync with ItemLedger
                from karar.models import ItemLedger
                ItemLedger.objects.filter(tcode=tcode, t_type='DC').delete()
                
                dcode_str = (master.dcode or '').strip()
                if dcode_str.startswith('1') or dcode_str == '7':
                    for det in details_data:
                        item_id = det.get('item_code')
                        if item_id:
                            qty_val = float(det.get('qty') or 0)
                            ItemLedger.objects.create(
                                tcode=tcode,
                                t_type='DC',
                                item_code=int(item_id),
                                t_date=master.inv_date,
                                in_qty=0,
                                out_qty=qty_val,
                                balance=-qty_val,
                                is_deleted='N'
                            )
                
                # Sync with InvoiceLedger
                from .models import InvoiceLedger
                InvoiceLedger.objects.filter(tcode=tcode).delete()
                if dcode_str.startswith('2'):
                    from django.db import connection
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT GET_INVOICE_AMOUNT(%s) FROM DUAL", [tcode])
                        row = cursor.fetchone()
                        total_amount = float(row[0] or 0.0) if row else 0.0
                    
                    InvoiceLedger.objects.create(
                        tcode=tcode,
                        inv_tcode=tcode,
                        tdate=master.inv_date,
                        dr=total_amount,
                        cr=0
                    )

                # Cascade updates on Edit (PUT)
                dc_synced_info = None
                inv_synced_info = None
                gl_synced_info = None

                if request.method == 'PUT':
                    # A. Editing Sales Invoice (dcode='2') -> Cascade to linked DC and GL
                    if dcode_str.startswith('2'):
                        linked_dc = None
                        if master.ref_tcode:
                            linked_dc = InvoiceMst.objects.filter(tcode=master.ref_tcode, dcode='1').first()
                        if not linked_dc and master.challan_no:
                            linked_dc = InvoiceMst.objects.filter(
                                dcode='1',
                                challan_no=master.challan_no,
                                cstcode=master.cstcode,
                                cmp_code=master.cmp_code
                            ).first()

                        if linked_dc:
                            if master.remarks:
                                linked_dc.remarks = master.remarks
                            linked_dc.modi_by = master.modi_by or 'admin'
                            linked_dc.save()

                            InvoiceDet.objects.filter(tcode=linked_dc.tcode).delete()
                            dc_det_objects = []
                            for det in details_data:
                                dc_det_objects.append(InvoiceDet(
                                    tcode=linked_dc,
                                    item_code_id=det.get('item_code'),
                                    qty=det.get('qty', 0),
                                    tmp_qty=det.get('tmp_qty', 0),
                                    rate=det.get('rate', 0),
                                    gst=det.get('gst', 0),
                                    dremarks=det.get('dremarks', ''),
                                ))
                            InvoiceDet.objects.bulk_create(dc_det_objects)

                            from karar.models import ItemLedger
                            ItemLedger.objects.filter(tcode=linked_dc.tcode, t_type='DC').delete()
                            for det in details_data:
                                item_id = det.get('item_code')
                                if item_id:
                                    qty_val = float(det.get('qty') or 0)
                                    ItemLedger.objects.create(
                                        tcode=linked_dc.tcode,
                                        t_type='DC',
                                        item_code=int(item_id),
                                        t_date=linked_dc.inv_date,
                                        in_qty=0,
                                        out_qty=qty_val,
                                        balance=-qty_val,
                                        is_deleted='N'
                                    )
                            dc_synced_info = f"DC #{linked_dc.challan_no or linked_dc.docno}"

                        if master.is_posted == 'Y' or master.gl_srno or sync_gl_dc:
                            try:
                                from django.db import connection
                                with connection.cursor() as django_cursor:
                                    raw_cursor = django_cursor.cursor.cursor
                                    out_srno = raw_cursor.var(int)
                                    raw_cursor.callproc('KARAR.PRC_INVOICE_GL_POST', [int(master.tcode), out_srno])
                                    gl_srno_val = out_srno.getvalue()
                                    if gl_srno_val:
                                        master.gl_srno = gl_srno_val
                                        master.is_posted = 'Y'
                                        master.save()
                                        gl_synced_info = f"GL Voucher #{gl_srno_val}"
                            except Exception as gl_err:
                                print(f"GL sync notice for invoice {master.tcode}: {gl_err}")

                    # B. Editing Delivery Challan (dcode='1') with sync_gl_dc -> Cascade to linked Invoice and GL
                    elif dcode_str.startswith('1') and sync_gl_dc:
                        linked_inv = InvoiceMst.objects.filter(ref_tcode=master.tcode, dcode='2').first()
                        if not linked_inv and master.invoice_no:
                            linked_inv = InvoiceMst.objects.filter(invoice_no=master.invoice_no, dcode='2').first()
                        if not linked_inv and master.challan_no:
                            linked_inv = InvoiceMst.objects.filter(
                                dcode='2',
                                challan_no=master.challan_no,
                                cstcode=master.cstcode,
                                cmp_code=master.cmp_code
                            ).first()

                        if linked_inv:
                            if master.remarks:
                                linked_inv.remarks = master.remarks
                            linked_inv.modi_by = master.modi_by or 'admin'
                            linked_inv.save()

                            InvoiceDet.objects.filter(tcode=linked_inv.tcode).delete()
                            inv_det_objects = []
                            for det in details_data:
                                inv_det_objects.append(InvoiceDet(
                                    tcode=linked_inv,
                                    item_code_id=det.get('item_code'),
                                    qty=det.get('qty', 0),
                                    tmp_qty=det.get('tmp_qty', 0),
                                    rate=det.get('rate', 0),
                                    gst=det.get('gst', 0),
                                    dremarks=det.get('dremarks', ''),
                                ))
                            InvoiceDet.objects.bulk_create(inv_det_objects)

                            from .models import InvoiceLedger
                            InvoiceLedger.objects.filter(tcode=linked_inv.tcode).delete()
                            from django.db import connection
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT GET_INVOICE_AMOUNT(%s) FROM DUAL", [linked_inv.tcode])
                                row = cursor.fetchone()
                                total_amount = float(row[0] or 0.0) if row else 0.0

                            InvoiceLedger.objects.create(
                                tcode=linked_inv.tcode,
                                inv_tcode=linked_inv.tcode,
                                tdate=linked_inv.inv_date,
                                dr=total_amount,
                                cr=0
                            )

                            if linked_inv.is_posted == 'Y' or linked_inv.gl_srno or sync_gl_dc:
                                try:
                                    from django.db import connection
                                    with connection.cursor() as django_cursor:
                                        raw_cursor = django_cursor.cursor.cursor
                                        out_srno = raw_cursor.var(int)
                                        raw_cursor.callproc('KARAR.PRC_INVOICE_GL_POST', [int(linked_inv.tcode), out_srno])
                                        gl_srno_val = out_srno.getvalue()
                                        if gl_srno_val:
                                            linked_inv.gl_srno = gl_srno_val
                                            linked_inv.is_posted = 'Y'
                                            linked_inv.save()
                                            gl_synced_info = f"GL Voucher #{gl_srno_val}"
                                except Exception as gl_err:
                                    print(f"GL sync notice for invoice {linked_inv.tcode}: {gl_err}")

                            inv_synced_info = f"Invoice #{linked_inv.invoice_no or linked_inv.docno}"

            sync_parts = ['DC updated successfully' if dcode_str.startswith('1') else 'Invoice updated successfully']
            if dc_synced_info:
                sync_parts.append(f"Linked {dc_synced_info} synchronized")
            if inv_synced_info:
                sync_parts.append(f"Linked {inv_synced_info} synchronized")
            if gl_synced_info:
                sync_parts.append(f"{gl_synced_info} synchronized")
            msg = ' | '.join(sync_parts) if request.method == 'PUT' else 'Record created successfully'

            return JsonResponse({
                'success': True,
                'message': msg,
                'tcode': master.tcode,
                'docno': master.docno,
                'challan_no': master.challan_no,
                'gl_srno': master.gl_srno,
                'dc_synced': bool(dc_synced_info),
                'inv_synced': bool(inv_synced_info),
                'gl_synced': bool(gl_synced_info)
            })
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'DELETE':
        try:
            from karar.models import ScUsername, ItemLedger
            from django.db import connection
            
            # Authorization Check: only Power Users (level 'A' or 'D') are allowed to delete
            usrid = request.session.get('usrid')
            user_profile = ScUsername.objects.filter(usrid=usrid).first()
            if not user_profile or user_profile.ulevl not in ['A', 'D']:
                return JsonResponse({
                    'success': False, 
                    'message': 'Permission Denied: Only Power Users (Administrators) are allowed to delete records.'
                }, status=403)

            data = json.loads(request.body)
            tcode = data.get('tcode')
            master = InvoiceMst.objects.get(tcode=tcode)
            
            # Prevent deleting invoiced Delivery Challans (check active non-deleted invoices)
            if (master.dcode or '').strip() == '1':
                is_invoiced = InvoiceMst.objects.exclude(is_deleted=1).filter(
                    dcode='2',
                    ref_tcode=master.tcode
                ).exists()
                if not is_invoiced and master.challan_no:
                    is_invoiced = InvoiceMst.objects.exclude(is_deleted=1).filter(
                        dcode='2',
                        challan_no=master.challan_no,
                        cstcode=master.cstcode,
                        cmp_code=master.cmp_code
                    ).exists()
                if is_invoiced:
                    return JsonResponse({
                        'success': False,
                        'message': 'This Delivery Challan has already been invoiced and cannot be deleted.'
                    })

            # Check if there are payments recorded against this invoice
            from .models import InvoiceLedger
            has_payments = InvoiceLedger.objects.filter(inv_tcode=tcode, cr__gt=0).exists()
            if has_payments:
                return JsonResponse({
                    'success': False,
                    'message': 'Permission Denied: This invoice has payments recorded against it and cannot be deleted.'
                })

            with transaction.atomic():
                with connection.cursor() as django_cursor:
                    # Perform Soft Delete in database
                    django_cursor.execute(
                        """
                        UPDATE INVOICE_MST 
                        SET IS_DELETED = 1, 
                            DELETED_BY = %s, 
                            DELETED_DATE = SYSDATE 
                        WHERE TCODE = %s
                        """,
                        [user_profile.descr or 'admin', int(tcode)]
                    )
                    
                    # Clean up stock impact by deleting associated ItemLedger entries
                    ItemLedger.objects.filter(tcode=tcode, t_type='DC').delete()
                    ItemLedger.objects.filter(tcode=tcode, t_type='INV').delete()
                    
                    # Delete associated InvoiceLedger entries
                    InvoiceLedger.objects.filter(tcode=tcode).delete()
                    InvoiceLedger.objects.filter(inv_tcode=tcode).delete()
                    
                    # If this invoice has already been posted to GL, cancel the GL voucher
                    if master.gl_srno:
                        django_cursor.execute(
                            "UPDATE GL_VOUCH_MST SET IS_POSTED = 'C', IS_DELETED = 1 WHERE SRNO = %s",
                            [master.gl_srno]
                        )
                        
            return JsonResponse({'success': True, 'message': 'Invoice deleted and cancelled successfully!'})
        except InvoiceMst.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Invoice not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=500)

@csrf_exempt
def invoice_payment_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    if request.method == 'GET':
        try:
            tcode_str = request.GET.get('tcode')
            if tcode_str:
                tcode = int(tcode_str)
                master = InvoicePaymentMst.objects.get(tcode=tcode)
                master_data = {
                    'tcode': master.tcode,
                    'docno': master.docno,
                    'dcode': master.dcode,
                    'tdate': master.tdate.isoformat() if master.tdate else None,
                    'cstcode': master.cstcode_id,
                    'manual_no': master.manual_no,
                    'challan_no': master.challan_no,
                    'cmp_code': master.cmp_code_id,
                    'payment_mode': master.payment_mode,
                    'acc_code': master.acc_code,
                    'cheque_no': master.cheque_no,
                    'cheque_date': master.cheque_date,
                    'dir_code': master.dir_code,
                    'remarks': master.remarks,
                    'tax_category': master.tax_category,
                    'is_posted': master.is_posted or 'N',
                    'gl_srno': master.gl_srno,
                }
                
                from django.db.models.expressions import RawSQL
                details = list(InvoicePaymentDet.objects.filter(tcode=tcode).annotate(
                    docno=RawSQL("GET_DOCNO(ref_tcode)", [])
                ).values(
                    'ref_tcode', 'docno', 'tmp_amount', 'bill_amount', 'tax_amount', 'ded_amount', 'st_add_rate', 'paid_amount', 'received_amount', 'tax_id'
                ))
                
                for d in details:
                    inv_obj = InvoiceMst.objects.filter(tcode=d['ref_tcode']).first()
                    if inv_obj:
                        d['manual_no'] = inv_obj.invoice_no
                        d['po_no'] = inv_obj.po_no or '---'
                        d['inv_date'] = inv_obj.inv_date.isoformat() if inv_obj.inv_date else None
                
                return JsonResponse({'success': True, 'master': master_data, 'details': details})
            else:
                start_date = request.GET.get('start_date')
                end_date = request.GET.get('end_date')
                cstcode = request.GET.get('cstcode')
                cmp_code = request.GET.get('cmp_code')
                acc_code = request.GET.get('acc_code')
                is_posted = request.GET.get('is_posted')
                
                query = InvoicePaymentMst.objects.exclude(is_deleted=1)
                
                if start_date and end_date:
                    query = query.filter(tdate__range=[start_date, end_date])
                if cstcode:
                    query = query.filter(cstcode_id=cstcode)
                if cmp_code:
                    query = query.filter(cmp_code_id=cmp_code)
                if acc_code:
                    query = query.filter(acc_code=acc_code)
                if is_posted:
                    if is_posted.upper() == 'Y':
                        query = query.filter(is_posted='Y')
                    elif is_posted.upper() == 'N':
                        query = query.filter(Q(is_posted='N') | Q(is_posted__isnull=True) | Q(is_posted=''))
                    
                payments = list(query.annotate(
                    tot_bill_amt=Sum('invoicepaymentdet__bill_amount'),
                    tot_rcv_amt=Sum('invoicepaymentdet__received_amount'),
                    tot_tax=Sum('invoicepaymentdet__tax_amount'),
                    tot_ded=Sum('invoicepaymentdet__ded_amount')
                ).order_by('-tcode').values(
                    'tcode', 'docno', 'tdate', 'cstcode__name', 'manual_no', 'payment_mode',
                    'acc_code',
                    'tot_bill_amt', 'tot_rcv_amt', 'tot_tax', 'tot_ded', 'cmp_code__cmp_name',
                    'is_posted'
                ))
                
                from karar.models import GlCoa
                acc_codes = [p['acc_code'] for p in payments if p.get('acc_code')]
                acc_map = {}
                if acc_codes:
                    acc_map = {str(a['acc_code']): a['acc_name'] for a in GlCoa.objects.filter(acc_code__in=acc_codes).values('acc_code', 'acc_name')}

                for p in payments:
                    ref_tcodes = list(InvoicePaymentDet.objects.filter(tcode=p['tcode']).values_list('ref_tcode', flat=True))
                    inv_nos = list(InvoiceMst.objects.filter(tcode__in=ref_tcodes).values_list('invoice_no', flat=True))
                    distinct_nos = [no for no in inv_nos if no]
                    p['invoice_no_str'] = ", ".join(dict.fromkeys(distinct_nos)) if distinct_nos else p['manual_no']
                    p['acc_name'] = acc_map.get(str(p.get('acc_code')), '')
                    if p.get('tdate'):
                        p['tdate'] = p['tdate'].isoformat() if hasattr(p['tdate'], 'isoformat') else str(p['tdate'])
                
                return JsonResponse({'success': True, 'payments': payments})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            master_data = data.get('master', {})
            details_data = data.get('details', [])
            
            with transaction.atomic():
                if not master_data.get('tcode'):
                    max_tcode = InvoicePaymentMst.objects.aggregate(Max('tcode'))['tcode__max']
                    tcode = (max_tcode or 0) + 1
                    
                    tdate_str = master_data.get('tdate')
                    docno = None
                    if tdate_str:
                        from django.db import connection
                        with connection.cursor() as django_cursor:
                            raw_cursor = django_cursor.cursor.cursor
                            # Get fiscal code
                            django_cursor.execute("SELECT GET_FISCALCODE(TO_DATE(%s, 'YYYY-MM-DD')) FROM DUAL", [tdate_str])
                            fiscal_code = django_cursor.fetchone()[0]
                            
                            # Call procedure GET_NEXT_SALES_TRNO with 'CIP'
                            out_sqno = raw_cursor.var(str)
                            raw_cursor.callproc('GET_NEXT_SALES_TRNO', ['CIP', fiscal_code, out_sqno])
                            docno = out_sqno.getvalue()
                    
                    master = InvoicePaymentMst.objects.create(
                        tcode=tcode,
                        docno=docno,
                        dcode=master_data.get('dcode', 'IP'),
                        tdate=master_data.get('tdate'),
                        cstcode_id=master_data.get('cstcode'),
                        manual_no=master_data.get('manual_no'),
                        challan_no=master_data.get('challan_no'),
                        cmp_code_id=master_data.get('cmp_code') or 1,
                        payment_mode=master_data.get('payment_mode'),
                        acc_code=master_data.get('acc_code'),
                        cheque_no=master_data.get('cheque_no'),
                        cheque_date=master_data.get('cheque_date'),
                        dir_code=master_data.get('dir_code'),
                        remarks=master_data.get('remarks'),
                        tax_category=master_data.get('tax_category'),
                        ent_by=request.session.get('descr') or request.session.get('username') or master_data.get('ent_by') or 'system',
                    )
                else:
                    tcode = master_data.get('tcode')
                    master = InvoicePaymentMst.objects.get(tcode=tcode)
                    
                    master.docno = master_data.get('docno', master.docno)
                    master.tdate = master_data.get('tdate', master.tdate)
                    master.cstcode_id = master_data.get('cstcode', master.cstcode_id)
                    master.manual_no = master_data.get('manual_no', master.manual_no)
                    master.challan_no = master_data.get('challan_no', master.challan_no)
                    master.cmp_code_id = master_data.get('cmp_code', master.cmp_code_id) or 1
                    master.payment_mode = master_data.get('payment_mode', master.payment_mode)
                    master.acc_code = master_data.get('acc_code', master.acc_code)
                    master.cheque_no = master_data.get('cheque_no', master.cheque_no)
                    master.cheque_date = master_data.get('cheque_date', master.cheque_date)
                    master.dir_code = master_data.get('dir_code', master.dir_code)
                    master.remarks = master_data.get('remarks', master.remarks)
                    master.tax_category = master_data.get('tax_category', master.tax_category)
                    master.modi_by = request.session.get('descr') or request.session.get('username') or master_data.get('modi_by') or 'system'
                    master.save()
                    
                    InvoicePaymentDet.objects.filter(tcode=tcode).delete()

                det_objects = []
                for det in details_data:
                    det_objects.append(InvoicePaymentDet(
                        tcode=master,
                        ref_tcode_id=det.get('ref_tcode'),
                        tmp_amount=det.get('tmp_amount', 0),
                        bill_amount=det.get('bill_amount', 0),
                        tax_amount=det.get('tax_amount', 0),
                        ded_amount=det.get('ded_amount', 0),
                        st_add_rate=det.get('st_add_rate', 0),
                        paid_amount=det.get('paid_amount', 0),
                        received_amount=det.get('received_amount', 0),
                        tax_id=det.get('tax_id'),
                    ))
                InvoicePaymentDet.objects.bulk_create(det_objects)
                
                # Sync with InvoiceLedger
                from .models import InvoiceLedger
                InvoiceLedger.objects.filter(tcode=master.tcode).delete()
                
                for det in details_data:
                    ref_tcode = det.get('ref_tcode')
                    if ref_tcode:
                        rcv_amt = float(det.get('received_amount') or 0)
                        tax_amt = float(det.get('tax_amount') or 0)
                        ded_amt = float(det.get('ded_amount') or 0)
                        total_cr = rcv_amt + tax_amt + ded_amt
                        
                        InvoiceLedger.objects.create(
                            tcode=master.tcode,
                            inv_tcode=int(ref_tcode),
                            tdate=master.tdate,
                            dr=0,
                            cr=total_cr
                        )
            
            return JsonResponse({'success': True, 'tcode': master.tcode, 'docno': master.docno, 'message': 'Payment saved successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    elif request.method == 'DELETE':
        try:
            from django.db import connection
            from karar.models import ScUsername
            
            # Authorization Check: only Power Users (level 'A' or 'D') are allowed to delete
            usrid = request.session.get('usrid')
            user_profile = ScUsername.objects.filter(usrid=usrid).first()
            if not user_profile or user_profile.ulevl not in ['A', 'D']:
                return JsonResponse({
                    'success': False, 
                    'message': 'Permission Denied: Only Power Users (Administrators) are allowed to delete records.'
                }, status=403)

            data = json.loads(request.body)
            tcode = data.get('tcode')
            
            with transaction.atomic():
                try:
                    mst = InvoicePaymentMst.objects.get(tcode=tcode)
                    
                    # Perform Soft Delete
                    with connection.cursor() as django_cursor:
                        django_cursor.execute(
                            """
                            UPDATE INVOICE_PAYMENT_MST 
                            SET IS_DELETED = 1, 
                                DELETED_BY = %s, 
                                DELETE_DATE = SYSDATE 
                            WHERE TCODE = %s
                            """,
                            [user_profile.descr or 'admin', int(tcode)]
                        )
                        
                        # Clean up InvoiceLedger payment records
                        from .models import InvoiceLedger
                        InvoiceLedger.objects.filter(tcode=tcode).delete()
                        
                        # If this payment has already been posted to GL, cancel the GL voucher
                        if mst.gl_srno:
                            django_cursor.execute(
                                "UPDATE GL_VOUCH_MST SET IS_POSTED = 'C', IS_DELETED = 1 WHERE SRNO = %s",
                                [mst.gl_srno]
                            )
                            
                    return JsonResponse({'success': True, 'message': 'Payment deleted and cancelled successfully!'})
                except InvoicePaymentMst.DoesNotExist:
                    return JsonResponse({'success': False, 'message': 'Payment not found for deletion.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=500)

@csrf_exempt
def outstanding_invoices_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    if request.method == 'GET':
        cstcode = request.GET.get('cstcode')
        cmp_code = request.GET.get('cmp_code')
        if not cstcode:
            return JsonResponse({'success': False, 'message': 'Customer code is required.'})
            
        try:
            from django.db.models.expressions import RawSQL
            from django.db.models import Q

            base_query = InvoiceMst.objects.filter(cstcode_id=cstcode, dcode__startswith='2').exclude(is_deleted=1)
            if cmp_code:
                base_query = base_query.filter(cmp_code_id=cmp_code)
            
            invoices = base_query.annotate(
                doc_no=RawSQL("GET_DOCNO(tcode)", [])
            )

            outstanding_list = []
            
            for inv in invoices:
                details = InvoiceDet.objects.filter(tcode=inv)
                bill_amount = 0.0
                for d in details:
                    base = float(d.qty or 0) * float(d.rate or 0)
                    gst_pct = float(d.gst or 0)
                    gst_amt = base * (gst_pct / 100.0)
                    bill_amount += base + gst_amt
                
                payments = InvoicePaymentDet.objects.filter(ref_tcode=inv).exclude(tcode__is_deleted=1)
                paid_amount = sum(float(p.received_amount or 0) + float(p.tax_amount or 0) + float(p.ded_amount or 0) for p in payments)
                
                balance = bill_amount - paid_amount
                
                if balance > 10:
                    outstanding_list.append({
                        'tcode': inv.tcode,
                        'docno': getattr(inv, 'doc_no', None) or inv.docno,
                        'po_no': inv.po_no or '---',
                        'inv_date': inv.inv_date.isoformat() if inv.inv_date else None,
                        'manual_no': inv.invoice_no,
                        'bill_amount': round(bill_amount, 2),
                        'paid_amount': round(paid_amount, 2),
                        'balance': round(balance, 2),
                        'dir_code': inv.dir_code,
                        'gstbase_flag': inv.gstbase_flag,
                    })
                    
            return JsonResponse({'success': True, 'invoices': outstanding_list})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})

    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=500)

@csrf_exempt
def invoice_print_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    from django.http import HttpResponse
    import io
    import os
    from django.conf import settings
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    if request.method == 'GET':
        tcode = request.GET.get('tcode')
        if not tcode:
            return HttpResponse("Missing tcode", status=400)
            
        is_dummy = request.GET.get('is_dummy') in ['true', '1', 'True', True] or request.GET.get('dummy') in ['true', '1', 'True', True]
            
        try:
            from .models import InvoiceMst, InvoiceDet
            master = InvoiceMst.objects.get(tcode=tcode)
            all_details = InvoiceDet.objects.filter(tcode=tcode).select_related('item_code')
            
            # If Dummy Invoice, only include items where dremarks IS NOT NULL
            if is_dummy:
                details = [d for d in all_details if d.dremarks and str(d.dremarks).strip() != '']
            else:
                details = list(all_details)
            
            calculated_details = []
            total_net_amount = 0
            total_qty = 0
            total_base_amt = 0
            total_gst_amt = 0
            
            for d in details:
                qty = float(d.qty or 0)
                rate = float(d.rate or 0)
                base_amt = qty * rate
                
                gst_pct = float(d.gst or 0)
                gst_amt = round(base_amt * (gst_pct / 100))
                
                net_amt = base_amt + gst_amt
                
                total_qty += qty
                total_base_amt += base_amt
                total_gst_amt += gst_amt
                total_net_amount += net_amt
                
                # If Dummy Invoice, use dremarks as item description
                if is_dummy:
                    item_display_name = str(d.dremarks).strip()
                else:
                    item_display_name = d.item_code.item_name2 if (d.item_code and d.item_code.item_name2) else (d.item_code.item_name if d.item_code else '')
                
                calculated_details.append({
                    'item_name': item_display_name,
                    'item_name2': d.item_code.item_name2 if d.item_code else '',
                    'dremarks': d.dremarks or '',
                    'qty': qty,
                    'rate': rate,
                    'base_amt': base_amt,
                    'gst_pct': gst_pct,
                    'gst_amt': gst_amt,
                    'net_amt': net_amt,
                })

            response = HttpResponse(content_type='application/pdf')
            pdf_fname = f"dummy_invoice_{tcode}.pdf" if is_dummy else f"invoice_{tcode}.pdf"
            response['Content-Disposition'] = f'inline; filename="{pdf_fname}"'
            
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=47, leftMargin=47, topMargin=30, bottomMargin=115)
            elements = []
            
            styles = getSampleStyleSheet()
            
            # --- PALETTE CONFIGURATION ---
            primary_color = colors.HexColor('#0f172a') # Deep Slate 900
            accent_color = colors.HexColor('#0284c7')  # Sky Blue 600
            border_color = colors.HexColor('#cbd5e1')  # Slate 300
            light_bg = colors.HexColor('#f8fafc')      # Slate 50
            alternate_row_color = colors.HexColor('#f1f5f9') # Slate 100
            text_dark = colors.HexColor('#1e293b')
            text_muted = colors.HexColor('#64748b')
            
            # Styles
            val_style = ParagraphStyle('Val', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=text_dark)
            val_bold_style = ParagraphStyle('ValBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=primary_color)
            val_right_style = ParagraphStyle('ValRight', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, alignment=TA_RIGHT, textColor=text_dark)
            val_center_style = ParagraphStyle('ValCenter', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, alignment=TA_CENTER, textColor=text_dark)
            val_center_bold_style = ParagraphStyle('ValCenterBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, alignment=TA_CENTER, textColor=primary_color)
            val_right_bold_style = ParagraphStyle('ValRightBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, alignment=TA_RIGHT, textColor=primary_color)
            
            th_style = ParagraphStyle('TH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white, alignment=TA_CENTER)
            th_left_style = ParagraphStyle('THL', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)
            
            comp = master.cmp_code
            is_karar = comp and comp.cmp_code == 1
            
            comp_name = comp.cmp_name if comp and comp.cmp_name else "Company"
            initials = "".join([w[0] for w in comp_name.split() if w and w[0].isalnum()]).upper()[:2]
            
            code_num = comp.cmp_code if comp and comp.cmp_code else 0
            cname_upper = comp_name.upper()
            
            logo_files = []
            if code_num == 1 or "KARAR" in cname_upper:
                logo_files = ['kt_logo.png', 'karar_logo.png', 'logo.png']
            elif code_num == 2 or "USMAN" in cname_upper:
                logo_files = ['us_logo.png', 'usman_sons_logo.png', 'logo.png']
            elif code_num == 3 or "R&W" in cname_upper or "RW" in cname_upper:
                logo_files = ['rw_logo.png', 'rw_brothers_logo.png', 'logo.png']
            else:
                logo_files = ['logo.png', 'kt_logo.png']

            search_dirs = [
                os.path.join(settings.BASE_DIR, 'static', 'logos'),
                os.path.join(settings.BASE_DIR, 'static', 'images'),
                os.path.join(settings.BASE_DIR, 'frontend', 'public', 'logos'),
                os.path.join(settings.BASE_DIR, 'frontend', 'dist', 'logos'),
            ]

            logo_w, logo_h = (80, 80) if (code_num in [2, 3] or "USMAN" in cname_upper or "R&W" in cname_upper or "RW" in cname_upper) else (65, 65)

            logo_img = None
            for lfile in logo_files:
                for sdir in search_dirs:
                    lpath = os.path.join(sdir, lfile)
                    if os.path.exists(lpath):
                        logo_img = Image(lpath, width=logo_w, height=logo_h)
                        break
                if logo_img:
                    break
            
            slogan = "DEALS IN ALL KIND OF IT COMPUTER OFFICE STATIONERY & GENERAL ITEM SUPPLY."
            
            comp_address = comp.address if comp and comp.address else ""
            comp_phone = f"Mob: {comp.mob_num}" if comp and comp.mob_num else ""
            if comp and comp.ptcl_num:
                comp_phone = f"{comp_phone} | Tel: {comp.ptcl_num}" if comp_phone else f"Tel: {comp.ptcl_num}"
                
            comp_ntn = comp.ntn_num if comp and comp.ntn_num else ("6491495-2" if is_karar else "")
            comp_strn = comp.saletax_num if comp and comp.saletax_num else ("3277876185775" if is_karar else "")
            
            tax_parts = []
            if comp_ntn:
                tax_parts.append(f"<b>NTN:</b> {comp_ntn}")
            if comp_strn:
                tax_parts.append(f"<b>STRN:</b> {comp_strn}")
            tax_str = "  |  ".join(tax_parts)
            
            header_left_data = [
                [Paragraph(f"<b><font size='15' color='#0f172a'>{comp_name}</font></b>", ParagraphStyle('CompName', parent=styles['Normal'], fontName='Helvetica-Bold', leading=17))],
                [Paragraph(f"<font size='7.5' color='#64748b'>{slogan}</font>", ParagraphStyle('CompSlogan', parent=styles['Normal'], fontName='Helvetica', leading=9.5))],
            ]
            if comp_address:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#334155'>{comp_address}</font>", ParagraphStyle('CompAddr', parent=styles['Normal'], fontName='Helvetica', leading=9.5))])
            if comp_phone:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#334155'>{comp_phone}</font>", ParagraphStyle('CompPhone', parent=styles['Normal'], fontName='Helvetica', leading=9.5))])
            if tax_str:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#1e3a8a'>{tax_str}</font>", ParagraphStyle('CompTax', parent=styles['Normal'], fontName='Helvetica', leading=10))])
                
            left_col_width = 290 - (logo_w + 5) if logo_img else 290
            header_left_text_table = Table(header_left_data, colWidths=[left_col_width])
            header_left_text_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
            ]))
            
            if logo_img:
                header_left_layout = Table([[logo_img, header_left_text_table]], colWidths=[logo_w + 5, left_col_width])
                header_left_layout.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('LEFTPADDING', (0,0), (-1,-1), 0),
                    ('RIGHTPADDING', (0,0), (-1,-1), 0),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                    ('TOPPADDING', (0,0), (-1,-1), 0),
                ]))
            else:
                header_left_layout = Table([[header_left_text_table]], colWidths=[290])
                header_left_layout.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'TOP'),
                    ('LEFTPADDING', (0,0), (-1,-1), 0),
                    ('RIGHTPADDING', (0,0), (-1,-1), 0),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                    ('TOPPADDING', (0,0), (-1,-1), 0),
                ]))
                
            title_text = "Sales Tax Invoice" if is_karar else "Invoice"
            title_style = ParagraphStyle('DocTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=18, leading=20, alignment=TA_RIGHT, textColor=primary_color)
            
            cust_name = master.cstcode.name if master.cstcode else "-"
            cust_ntn = master.cstcode.ntn if master.cstcode and master.cstcode.ntn else ""
            cust_strn = master.cstcode.srb if master.cstcode and master.cstcode.srb else ""
            
            inv_date_str = master.inv_date.strftime('%d-%b-%Y').upper() if master.inv_date else ''
            po_no_str = master.po_no or "-"
            challan_no_str = master.challan_no or ""
            doc_no_str = master.invoice_no or ""
            
            meta_rows = [
                [Paragraph("<b>Date:</b>", val_style), Paragraph(inv_date_str, val_style)],
                [Paragraph("<b>Invoice #:</b>", val_style), Paragraph(doc_no_str, val_style)],
                [Paragraph("<b>PO #:</b>", val_style), Paragraph(po_no_str, val_style)],
                [Paragraph("<b>Challan #:</b>", val_style), Paragraph(challan_no_str, val_style)]
            ]
            meta_box_table = Table(meta_rows, colWidths=[65, 115])
            meta_box_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BACKGROUND', (0,0), (0,-1), light_bg),
                ('GRID', (0,0), (-1,-1), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,-1), 2.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ]))
            
            header_right_layout = [
                Paragraph(f"<b>{title_text.upper()}</b>", title_style),
                Spacer(1, 4),
                meta_box_table
            ]
            
            top_header_table = Table([[header_left_layout, header_right_layout]], colWidths=[290, 210])
            top_header_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))
            elements.append(top_header_table)
            elements.append(Spacer(1, 10))
            elements.append(HRFlowable(width="100%", thickness=1.5, color=accent_color, spaceBefore=2, spaceAfter=12))
            
            # --- BILL TO SECTION ---
            cust_address = master.cstcode.address if master.cstcode and master.cstcode.address else ""
            cust_phone = master.cstcode.phone_number if master.cstcode and master.cstcode.phone_number else ""
            
            left_flowables = []
            left_flowables.append(Paragraph(f"<b>{cust_name}</b>", val_bold_style))
            if cust_address:
                left_flowables.append(Paragraph(cust_address, val_style))
            if cust_phone:
                left_flowables.append(Paragraph(f"Phone: {cust_phone}", val_style))
                
            right_flowables = []
            right_flowables.append(Paragraph(f"<b>NTN:</b> {cust_ntn if cust_ntn else '-'}", val_style))
            right_flowables.append(Paragraph(f"<b>STN/SRB:</b> {cust_strn if cust_strn else '-'}", val_style))
            
            inner_table = Table([[left_flowables, right_flowables]], colWidths=[340, 140])
            inner_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))
            bill_to_content = [inner_table]
                
            bill_to_table = Table([
                [Paragraph("<b>BILL TO:</b>", ParagraphStyle('BTH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.white))],
                [bill_to_content]
            ], colWidths=[500])
            bill_to_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BACKGROUND', (0,0), (-1,0), primary_color),
                ('BOX', (0,0), (-1,-1), 0.5, border_color),
                ('GRID', (0,0), (-1,0), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,0), 3),
                ('BOTTOMPADDING', (0,0), (-1,0), 3),
                ('LEFTPADDING', (0,0), (-1,0), 8),
                ('RIGHTPADDING', (0,0), (-1,0), 8),
                ('TOPPADDING', (0,1), (-1,1), 6),
                ('BOTTOMPADDING', (0,1), (-1,1), 6),
                ('LEFTPADDING', (0,1), (-1,1), 8),
                ('RIGHTPADDING', (0,1), (-1,1), 8),
                ('BACKGROUND', (0,1), (-1,1), light_bg),
            ]))
            elements.append(bill_to_table)
            elements.append(Spacer(1, 12))
            
            # --- ITEMS TABLE ---
            if not is_karar:
                table_headers = [
                    Paragraph("<b>S#</b>", th_style),
                    Paragraph("<b>Item & Description</b>", th_left_style),
                    Paragraph("<b>Qty</b>", th_style),
                    Paragraph("<b>Unit Price (PKR)</b>", th_style),
                    Paragraph("<b>Total Amount (PKR)</b>", th_style)
                ]
                table_data = [table_headers]
                
                for idx, row in enumerate(calculated_details):
                    table_data.append([
                        Paragraph(str(idx+1), val_center_style),
                        Paragraph(row['item_name'] or '', val_style),
                        Paragraph(f"{row['qty']:,.0f}", val_center_style),
                        Paragraph(f"{row['rate']:,.2f}", val_right_style),
                        Paragraph(f"{row['net_amt']:,.2f}", val_right_style),
                    ])
                
                table_data.append([
                    Paragraph("<b>Total Amount</b>", val_center_bold_style),
                    "", "", "",
                    Paragraph(f"<b>{total_net_amount:,.2f}</b>", val_right_bold_style)
                ])
                
                items_table = Table(table_data, colWidths=[30, 280, 40, 75, 75])
                items_table_style = TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                    ('GRID', (0, 0), (-1, -1), 0.5, border_color),
                    ('SPAN', (0, -1), (3, -1)),
                    ('BACKGROUND', (0, -1), (-1, -1), alternate_row_color),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ])
                for i in range(1, len(calculated_details) + 1):
                    if i % 2 == 0:
                        items_table_style.add('BACKGROUND', (0, i), (-1, i), light_bg)
                items_table.setStyle(items_table_style)
            else:
                table_headers = [
                    Paragraph("<b>S#</b>", th_style),
                    Paragraph("<b>Item & Description</b>", th_left_style),
                    Paragraph("<b>Qty</b>", th_style),
                    Paragraph("<b>Unit Price</b>", th_style),
                    Paragraph("<b>Total Excl. S.T</b>", th_style),
                    Paragraph("<b>GST %</b>", th_style),
                    Paragraph("<b>GST Amount</b>", th_style),
                    Paragraph("<b>Total Price Inc. S.T</b>", th_style)
                ]
                table_data = [table_headers]
                
                for idx, row in enumerate(calculated_details):
                    table_data.append([
                        Paragraph(str(idx+1), val_center_style),
                        Paragraph(row['item_name'] or '', val_style),
                        Paragraph(f"{row['qty']:,.0f}", val_center_style),
                        Paragraph(f"{row['rate']:,.2f}", val_right_style),
                        Paragraph(f"{row['base_amt']:,.2f}", val_right_style),
                        Paragraph(f"{row['gst_pct']:,.0f} %", val_center_style),
                        Paragraph(f"{row['gst_amt']:,.2f}", val_right_style),
                        Paragraph(f"{row['net_amt']:,.2f}", val_right_style),
                    ])
                
                table_data.append([
                    Paragraph("<b>Total Amount</b>", val_center_bold_style),
                    "", "", "", "", "", "",
                    Paragraph(f"<b>{total_net_amount:,.2f}</b>", val_right_bold_style)
                ])
                
                items_table = Table(table_data, colWidths=[25, 155, 35, 55, 75, 35, 55, 65])
                items_table_style = TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                    ('GRID', (0, 0), (-1, -1), 0.5, border_color),
                    ('SPAN', (0, -1), (6, -1)),
                    ('BACKGROUND', (0, -1), (-1, -1), alternate_row_color),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ])
                for i in range(1, len(calculated_details) + 1):
                    if i % 2 == 0:
                        items_table_style.add('BACKGROUND', (0, i), (-1, i), light_bg)
                items_table.setStyle(items_table_style)
                
            elements.append(items_table)
            elements.append(Spacer(1, 15))
            
            # --- FOOTER SECTION ---
            remarks_text = (master.remarks or "").strip()
            if remarks_text:
                remarks_html = remarks_text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
            else:
                remarks_html = "No special instructions."
            remarks_box = Table([
                [Paragraph("<b>Special Notes / Instructions</b>", ParagraphStyle('SBH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white))],
                [Paragraph(remarks_html, val_style)]
            ], colWidths=[290])
            remarks_box.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BACKGROUND', (0,0), (0,0), colors.HexColor('#64748b')),
                ('BOX', (0,0), (-1,-1), 0.5, border_color),
                ('GRID', (0,0), (-1,0), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,0), 3),
                ('BOTTOMPADDING', (0,0), (-1,0), 3),
                ('LEFTPADDING', (0,0), (-1,0), 6),
                ('TOPPADDING', (0,1), (-1,1), 6),
                ('BOTTOMPADDING', (0,1), (-1,1), 6),
                ('LEFTPADDING', (0,1), (-1,1), 6),
                ('RIGHTPADDING', (0,1), (-1,1), 6),
                ('BACKGROUND', (0,1), (-1,1), light_bg),
            ]))
            
            discount_val = float(getattr(master, 'discount', 0) or 0)
            delivery_charges_val = float(getattr(master, 'delivery_charges', 0) or 0)
            final_invoice_amount = max(0.0, total_net_amount - discount_val + delivery_charges_val)

            if is_karar:
                total_rows = [
                    [Paragraph("<b>Subtotal (Excl. Tax):</b>", val_style), Paragraph(f"{total_base_amt:,.2f}", val_right_style)],
                    [Paragraph("<b>Sales Tax (GST):</b>", val_style), Paragraph(f"{total_gst_amt:,.2f}", val_right_style)],
                ]
                if discount_val > 0:
                    total_rows.append([Paragraph("<b>Discount (-):</b>", val_style), Paragraph(f"<font color='#dc2626'>-{discount_val:,.2f}</font>", val_right_style)])
                if delivery_charges_val > 0:
                    total_rows.append([Paragraph("<b>Delivery Charges (+):</b>", val_style), Paragraph(f"<font color='#059669'>+{delivery_charges_val:,.2f}</font>", val_right_style)])
                total_rows.append([Paragraph("<b>Total Inc. Tax:</b>", val_style), Paragraph(f"<b>{final_invoice_amount:,.2f}</b>", val_right_bold_style)])
            else:
                total_rows = []
                if discount_val > 0 or delivery_charges_val > 0:
                    total_rows.append([Paragraph("<b>Items Total:</b>", val_style), Paragraph(f"{total_net_amount:,.2f}", val_right_style)])
                    if discount_val > 0:
                        total_rows.append([Paragraph("<b>Discount (-):</b>", val_style), Paragraph(f"<font color='#dc2626'>-{discount_val:,.2f}</font>", val_right_style)])
                    if delivery_charges_val > 0:
                        total_rows.append([Paragraph("<b>Delivery Charges (+):</b>", val_style), Paragraph(f"<font color='#059669'>+{delivery_charges_val:,.2f}</font>", val_right_style)])
                total_rows.append([Paragraph("<b>Total Amount:</b>", val_style), Paragraph(f"<b>{final_invoice_amount:,.2f}</b>", val_right_bold_style)])
            
            totals_box_table = Table(total_rows, colWidths=[100, 100])
            totals_box_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BACKGROUND', (0,0), (0,-1), light_bg),
                ('GRID', (0,0), (-1,-1), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,-1), 4.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ]))
            
            footer_layout_table = Table([[remarks_box, totals_box_table]], colWidths=[300, 200])
            footer_layout_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))
            
            sig_table = Table([
                ["", ""],
                ["________________________", "________________________"],
                ["Prepared By:", "Received By:"]
            ], colWidths=[245, 255])
            sig_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TEXTCOLOR', (0,2), (-1,2), text_dark),
                ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
                ('FONTSIZE', (0,2), (-1,2), 8.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2),
                ('TOPPADDING', (0,1), (-1,1), 2),
            ]))

            def draw_footer_canvas(canvas, doc):
                canvas.saveState()
                sig_table.wrapOn(canvas, 535, 35)
                sig_table.drawOn(canvas, 30, 68)
                canvas.setStrokeColor(colors.HexColor('#cbd5e1'))
                canvas.setLineWidth(0.8)
                canvas.line(30, 58, 565, 58)
                canvas.setFont('Helvetica-Bold', 8.5)
                canvas.setFillColor(primary_color)
                canvas.drawCentredString(297.5, 43, "Office Address: At ONB, B-9/C, 2nd Floor, Block-B, Mehersons Estate, Talpur Road, Karachi-Pakistan.")
                canvas.drawCentredString(297.5, 30, "Tel: +92-213-2460500 Mob: +92-300-2346993")
                canvas.restoreState()

            elements.append(footer_layout_table)
            
            doc.build(elements, onFirstPage=draw_footer_canvas, onLaterPages=draw_footer_canvas)
            
            pdf = buffer.getvalue()
            buffer.close()
            response.write(pdf)
            return response
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return HttpResponse(f"Error generating PDF: {str(e)}", status=500)
    
    return HttpResponse("Method not allowed", status=405)

@csrf_exempt
def dc_print_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    from django.http import HttpResponse
    import io
    import os
    from django.conf import settings
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    if request.method == 'GET':
        tcode = request.GET.get('tcode')
        if not tcode:
            return HttpResponse("Missing tcode", status=400)
            
        is_dummy = request.GET.get('is_dummy') in ['true', '1', 'True', True] or request.GET.get('dummy') in ['true', '1', 'True', True]
            
        try:
            from .models import InvoiceMst, InvoiceDet
            master = InvoiceMst.objects.get(tcode=tcode)
            all_details = InvoiceDet.objects.filter(tcode=tcode).select_related('item_code')
            
            # If Dummy DC, only include items where dremarks IS NOT NULL
            if is_dummy:
                details = [d for d in all_details if d.dremarks and str(d.dremarks).strip() != '']
            else:
                details = list(all_details)
            
            response = HttpResponse(content_type='application/pdf')
            pdf_fname = f"dummy_dc_{tcode}.pdf" if is_dummy else f"dc_{tcode}.pdf"
            response['Content-Disposition'] = f'inline; filename="{pdf_fname}"'
            
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=47, leftMargin=47, topMargin=30, bottomMargin=115)
            elements = []
            
            styles = getSampleStyleSheet()
            
            # --- PALETTE CONFIGURATION ---
            primary_color = colors.HexColor('#0f172a') # Deep Slate 900
            accent_color = colors.HexColor('#0284c7')  # Sky Blue 600
            border_color = colors.HexColor('#cbd5e1')  # Slate 300
            light_bg = colors.HexColor('#f8fafc')      # Slate 50
            alternate_row_color = colors.HexColor('#f1f5f9') # Slate 100
            text_dark = colors.HexColor('#1e293b')
            text_muted = colors.HexColor('#64748b')
            
            # Styles
            val_style = ParagraphStyle('Val', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, textColor=text_dark)
            val_bold_style = ParagraphStyle('ValBold', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=primary_color)
            val_center_style = ParagraphStyle('ValCenter', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=10, alignment=TA_CENTER, textColor=text_dark)
            
            th_style = ParagraphStyle('TH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white, alignment=TA_CENTER)
            th_left_style = ParagraphStyle('THL', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)
            
            comp = master.cmp_code
            is_karar = comp and comp.cmp_code == 1
            
            comp_name = comp.cmp_name if comp and comp.cmp_name else "Company"
            initials = "".join([w[0] for w in comp_name.split() if w and w[0].isalnum()]).upper()[:2]
            
            code_num = comp.cmp_code if comp and comp.cmp_code else 0
            cname_upper = comp_name.upper()
            
            logo_files = []
            if code_num == 1 or "KARAR" in cname_upper:
                logo_files = ['kt_logo.png', 'karar_logo.png', 'logo.png']
            elif code_num == 2 or "USMAN" in cname_upper:
                logo_files = ['us_logo.png', 'usman_sons_logo.png', 'logo.png']
            elif code_num == 3 or "R&W" in cname_upper or "RW" in cname_upper:
                logo_files = ['rw_logo.png', 'rw_brothers_logo.png', 'logo.png']
            else:
                logo_files = ['logo.png', 'kt_logo.png']

            search_dirs = [
                os.path.join(settings.BASE_DIR, 'static', 'logos'),
                os.path.join(settings.BASE_DIR, 'static', 'images'),
                os.path.join(settings.BASE_DIR, 'frontend', 'public', 'logos'),
                os.path.join(settings.BASE_DIR, 'frontend', 'dist', 'logos'),
            ]

            logo_w, logo_h = (80, 80) if (code_num in [2, 3] or "USMAN" in cname_upper or "R&W" in cname_upper or "RW" in cname_upper) else (65, 65)

            logo_img = None
            for lfile in logo_files:
                for sdir in search_dirs:
                    lpath = os.path.join(sdir, lfile)
                    if os.path.exists(lpath):
                        logo_img = Image(lpath, width=logo_w, height=logo_h)
                        break
                if logo_img:
                    break
            
            slogan = "DEALS IN ALL KIND OF IT COMPUTER OFFICE STATIONERY & GENERAL ITEM SUPPLY."
            
            comp_address = comp.address if comp and comp.address else ""
            comp_phone = f"Mob: {comp.mob_num}" if comp and comp.mob_num else ""
            if comp and comp.ptcl_num:
                comp_phone = f"{comp_phone} | Tel: {comp.ptcl_num}" if comp_phone else f"Tel: {comp.ptcl_num}"
                
            comp_ntn = comp.ntn_num if comp and comp.ntn_num else ("6491495-2" if is_karar else "")
            comp_strn = comp.saletax_num if comp and comp.saletax_num else ("3277876185775" if is_karar else "")
            
            tax_parts = []
            if comp_ntn:
                tax_parts.append(f"<b>NTN:</b> {comp_ntn}")
            if comp_strn:
                tax_parts.append(f"<b>STRN:</b> {comp_strn}")
            tax_str = "  |  ".join(tax_parts)
            
            header_left_data = [
                [Paragraph(f"<b><font size='15' color='#0f172a'>{comp_name}</font></b>", ParagraphStyle('CompName', parent=styles['Normal'], fontName='Helvetica-Bold', leading=17))],
                [Paragraph(f"<font size='7.5' color='#64748b'>{slogan}</font>", ParagraphStyle('CompSlogan', parent=styles['Normal'], fontName='Helvetica', leading=9.5))],
            ]
            if comp_address:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#334155'>{comp_address}</font>", ParagraphStyle('CompAddr', parent=styles['Normal'], fontName='Helvetica', leading=9.5))])
            if comp_phone:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#334155'>{comp_phone}</font>", ParagraphStyle('CompPhone', parent=styles['Normal'], fontName='Helvetica', leading=9.5))])
            if tax_str:
                header_left_data.append([Paragraph(f"<font size='7.5' color='#1e3a8a'>{tax_str}</font>", ParagraphStyle('CompTax', parent=styles['Normal'], fontName='Helvetica', leading=10))])
                
            left_col_width = 290 - (logo_w + 5) if logo_img else 290
            header_left_text_table = Table(header_left_data, colWidths=[left_col_width])
            header_left_text_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
            ]))
            
            if logo_img:
                header_left_layout = Table([[logo_img, header_left_text_table]], colWidths=[logo_w + 5, left_col_width])
                header_left_layout.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('LEFTPADDING', (0,0), (-1,-1), 0),
                    ('RIGHTPADDING', (0,0), (-1,-1), 0),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                    ('TOPPADDING', (0,0), (-1,-1), 0),
                ]))
            else:
                header_left_layout = Table([[header_left_text_table]], colWidths=[290])
                header_left_layout.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'TOP'),
                    ('LEFTPADDING', (0,0), (-1,-1), 0),
                    ('RIGHTPADDING', (0,0), (-1,-1), 0),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                    ('TOPPADDING', (0,0), (-1,-1), 0),
                ]))
                
            title_style = ParagraphStyle('DocTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=18, leading=20, alignment=TA_RIGHT, textColor=primary_color)
            
            cust_name = master.cstcode.name if master.cstcode else "-"
            cust_ntn = master.cstcode.ntn if master.cstcode and master.cstcode.ntn else ""
            cust_strn = master.cstcode.srb if master.cstcode and master.cstcode.srb else ""
            
            inv_date_str = master.inv_date.strftime('%d-%b-%Y').upper() if master.inv_date else ''
            po_no_str = master.po_no or "-"
            challan_no_str = master.challan_no or ""
            
            meta_rows = [
                [Paragraph("<b>Date:</b>", val_style), Paragraph(inv_date_str, val_style)],
                [Paragraph("<b>Challan #:</b>", val_style), Paragraph(challan_no_str, val_style)],
                [Paragraph("<b>PO #:</b>", val_style), Paragraph(po_no_str, val_style)]
            ]
            meta_box_table = Table(meta_rows, colWidths=[65, 115])
            meta_box_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BACKGROUND', (0,0), (0,-1), light_bg),
                ('GRID', (0,0), (-1,-1), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,-1), 2.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ]))
            
            header_right_layout = [
                Paragraph("<b>DELIVERY CHALLAN</b>", title_style),
                Spacer(1, 4),
                meta_box_table
            ]
            
            top_header_table = Table([[header_left_layout, header_right_layout]], colWidths=[290, 210])
            top_header_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))
            elements.append(top_header_table)
            elements.append(Spacer(1, 10))
            elements.append(HRFlowable(width="100%", thickness=1.5, color=accent_color, spaceBefore=2, spaceAfter=12))
            
            # --- CUSTOMER SECTION ---
            cust_address = master.cstcode.address if master.cstcode and master.cstcode.address else ""
            cust_phone = master.cstcode.phone_number if master.cstcode and master.cstcode.phone_number else ""
            
            left_flowables = []
            left_flowables.append(Paragraph(f"<b>{cust_name}</b>", val_bold_style))
            if cust_address:
                left_flowables.append(Paragraph(cust_address, val_style))
            if cust_phone:
                left_flowables.append(Paragraph(f"Phone: {cust_phone}", val_style))
                
            right_flowables = []
            right_flowables.append(Paragraph(f"<b>NTN:</b> {cust_ntn if cust_ntn else '-'}", val_style))
            right_flowables.append(Paragraph(f"<b>STN/SRB:</b> {cust_strn if cust_strn else '-'}", val_style))
            
            inner_table = Table([[left_flowables, right_flowables]], colWidths=[340, 140])
            inner_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0),
                ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))
            bill_to_content = [inner_table]
                
            bill_to_table = Table([
                [Paragraph("<b>CUSTOMER:</b>", ParagraphStyle('BTH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=10, textColor=colors.white))],
                [bill_to_content]
            ], colWidths=[500])
            bill_to_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BACKGROUND', (0,0), (-1,0), primary_color),
                ('BOX', (0,0), (-1,-1), 0.5, border_color),
                ('GRID', (0,0), (-1,0), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,0), 3),
                ('BOTTOMPADDING', (0,0), (-1,0), 3),
                ('LEFTPADDING', (0,0), (-1,0), 8),
                ('RIGHTPADDING', (0,0), (-1,0), 8),
                ('TOPPADDING', (0,1), (-1,1), 6),
                ('BOTTOMPADDING', (0,1), (-1,1), 6),
                ('LEFTPADDING', (0,1), (-1,1), 8),
                ('RIGHTPADDING', (0,1), (-1,1), 8),
                ('BACKGROUND', (0,1), (-1,1), light_bg),
            ]))
            elements.append(bill_to_table)
            elements.append(Spacer(1, 12))
            
            # --- ITEMS TABLE ---
            table_headers = [
                Paragraph("<b>S#</b>", th_style),
                Paragraph("<b>Item & Description</b>", th_left_style),
                Paragraph("<b>Qty</b>", th_style)
            ]
            table_data = [table_headers]
            
            for idx, d in enumerate(details):
                qty_val = float(d.qty or 0)
                if is_dummy:
                    item_display_name = str(d.dremarks).strip()
                else:
                    item_display_name = (d.item_code.item_name2 or d.item_code.item_name) if d.item_code else ''
                
                table_data.append([
                    Paragraph(str(idx+1), val_center_style),
                    Paragraph(item_display_name, val_style),
                    Paragraph(f"{qty_val:,.0f}", val_center_style),
                ])
                
            items_table = Table(table_data, colWidths=[30, 400, 70])
            items_table_style = TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('GRID', (0, 0), (-1, -1), 0.5, border_color),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ])
            for i in range(1, len(details) + 1):
                if i % 2 == 0:
                    items_table_style.add('BACKGROUND', (0, i), (-1, i), light_bg)
            items_table.setStyle(items_table_style)
            
            elements.append(items_table)
            elements.append(Spacer(1, 15))
            
            # --- REMARKS SECTION ---
            remarks_text = (master.remarks or "").strip()
            if master.dcode == '2' and master.ref_tcode:
                try:
                    dc_master = InvoiceMst.objects.get(tcode=master.ref_tcode)
                    remarks_text = (dc_master.remarks or "").strip()
                except InvoiceMst.DoesNotExist:
                    pass
            if remarks_text:
                remarks_html = remarks_text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')
            else:
                remarks_html = "No special instructions."
            remarks_box = Table([
                [Paragraph("<b>Special Notes / Instructions</b>", ParagraphStyle('SBH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white))],
                [Paragraph(remarks_html, val_style)]
            ], colWidths=[500])
            remarks_box.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('BACKGROUND', (0,0), (0,0), colors.HexColor('#64748b')),
                ('BOX', (0,0), (-1,-1), 0.5, border_color),
                ('GRID', (0,0), (-1,0), 0.5, border_color),
                ('TOPPADDING', (0,0), (-1,0), 3),
                ('BOTTOMPADDING', (0,0), (-1,0), 3),
                ('LEFTPADDING', (0,0), (-1,0), 6),
                ('TOPPADDING', (0,1), (-1,1), 6),
                ('BOTTOMPADDING', (0,1), (-1,1), 6),
                ('LEFTPADDING', (0,1), (-1,1), 6),
                ('RIGHTPADDING', (0,1), (-1,1), 6),
                ('BACKGROUND', (0,1), (-1,1), light_bg),
            ]))
            
            sig_table = Table([
                ["", ""],
                ["________________________", "________________________"],
                ["Prepared By:", "Received By:"]
            ], colWidths=[245, 255])
            sig_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TEXTCOLOR', (0,2), (-1,2), text_dark),
                ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
                ('FONTSIZE', (0,2), (-1,2), 8.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2),
                ('TOPPADDING', (0,1), (-1,1), 2),
            ]))

            def draw_footer_canvas(canvas, doc):
                canvas.saveState()
                sig_table.wrapOn(canvas, 535, 35)
                sig_table.drawOn(canvas, 30, 68)
                canvas.setStrokeColor(colors.HexColor('#cbd5e1'))
                canvas.setLineWidth(0.8)
                canvas.line(30, 58, 565, 58)
                canvas.setFont('Helvetica-Bold', 8.5)
                canvas.setFillColor(primary_color)
                canvas.drawCentredString(297.5, 43, "Office Address: At ONB, B-9/C, 2nd Floor, Block-B, Mehersons Estate, Talpur Road, Karachi-Pakistan.")
                canvas.drawCentredString(297.5, 30, "Tel: +92-213-2460500 Mob: +92-300-2346993")
                canvas.restoreState()

            elements.append(remarks_box)
            
            doc.build(elements, onFirstPage=draw_footer_canvas, onLaterPages=draw_footer_canvas)
            
            pdf = buffer.getvalue()
            buffer.close()
            response.write(pdf)
            return response
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return HttpResponse(f"Error generating PDF: {str(e)}", status=500)
            
    return HttpResponse("Method not allowed", status=405)


@csrf_exempt
def sales_invoices_for_return_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    
    if request.method == 'GET':
        try:
            start_date = request.GET.get('start_date')
            end_date = request.GET.get('end_date')
            cstcode = request.GET.get('cstcode')
            cmp_code = request.GET.get('cmp_code')
            
            if not start_date or not end_date or not cstcode or not cmp_code:
                return JsonResponse({'success': False, 'message': 'Missing filters.'})
            
            from django.db.models import ExpressionWrapper, DecimalField
            invoices = list(InvoiceMst.objects.exclude(is_deleted=1).filter(
                dcode='2',
                cstcode_id=cstcode,
                cmp_code_id=cmp_code,
                inv_date__range=[start_date, end_date]
            ).values('tcode', 'docno', 'inv_date', 'invoice_no', 'remarks'))
            
            for inv in invoices:
                details = InvoiceDet.objects.filter(tcode_id=inv['tcode'])
                bill_amt = sum((float(d.qty or 0) * float(d.rate or 0)) + ((float(d.gst or 0) / 100.0) * (float(d.qty or 0) * float(d.rate or 0))) for d in details)
                inv['bill_amount'] = round(bill_amt, 2)
                
            return JsonResponse({'success': True, 'invoices': invoices})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=405)


@csrf_exempt
def sales_invoice_details_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
        
    if request.method == 'GET':
        try:
            invoice_tcode = request.GET.get('invoice_tcode')
            if not invoice_tcode:
                return JsonResponse({'success': False, 'message': 'Invoice tcode is required.'})
            
            items = list(InvoiceDet.objects.filter(tcode_id=invoice_tcode).values(
                'item_code_id', 'item_code__item_name', 'qty', 'rate'
            ))
            
            for item in items:
                returned = InvoiceDet.objects.filter(
                    tcode__ref_tcode=invoice_tcode,
                    tcode__dcode='3',
                    item_code_id=item['item_code_id']
                ).aggregate(total_qty=Sum('qty'))
                
                item['prev_returned_qty'] = returned['total_qty'] or 0
                
            return JsonResponse({'success': True, 'items': items})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=405)


@csrf_exempt
def sales_return_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
        
    if request.method == 'GET':
        try:
            tcode = request.GET.get('tcode')
            if tcode:
                master = InvoiceMst.objects.filter(tcode=tcode, dcode='3').values(
                    'tcode', 'docno', 'inv_date', 'cstcode_id', 'cmp_code_id', 'remarks', 'ref_tcode'
                ).first()
                if not master:
                    return JsonResponse({'success': False, 'message': 'Sales return not found.'})
                    
                details = list(InvoiceDet.objects.filter(tcode_id=tcode).values(
                    'item_code_id', 'item_code__item_name', 'qty', 'rate', 'dremarks'
                ))
                
                original_tcode = master['ref_tcode']
                for d in details:
                    orig_item = InvoiceDet.objects.filter(tcode_id=original_tcode, item_code_id=d['item_code_id']).first()
                    d['orig_qty'] = orig_item.qty if orig_item else 0
                    
                    returned = InvoiceDet.objects.filter(
                        tcode__ref_tcode=original_tcode,
                        tcode__dcode='3',
                        item_code_id=d['item_code_id']
                    ).exclude(tcode_id=tcode).aggregate(total_qty=Sum('qty'))
                    
                    d['prev_returned_qty'] = returned['total_qty'] or 0
                    
                return JsonResponse({'success': True, 'master': master, 'details': details})
            else:
                start_date = request.GET.get('start_date')
                end_date = request.GET.get('end_date')
                cstcode = request.GET.get('cstcode')
                cmp_code = request.GET.get('cmp_code')
                
                query = InvoiceMst.objects.filter(dcode='3')
                if start_date and end_date:
                    query = query.filter(inv_date__range=[start_date, end_date])
                if cstcode:
                    query = query.filter(cstcode_id=cstcode)
                if cmp_code:
                    query = query.filter(cmp_code_id=cmp_code)
                    
                returns = list(query.order_by('-tcode').values(
                    'tcode', 'docno', 'inv_date', 'cstcode__name', 'cmp_code__cmp_name', 'remarks', 'ref_tcode'
                ))
                
                for r in returns:
                    orig = InvoiceMst.objects.filter(tcode=r['ref_tcode']).first()
                    r['orig_docno'] = orig.docno if orig else 'N/A'
                    
                return JsonResponse({'success': True, 'returns': returns})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
            
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            master_data = data.get('master', {})
            details_data = data.get('details', [])
            
            remarks = master_data.get('remarks')
            if not remarks or not remarks.strip():
                return JsonResponse({'success': False, 'message': 'Remarks are mandatory.'}, status=400)
                
            ref_tcode = master_data.get('ref_tcode')
            if not ref_tcode:
                return JsonResponse({'success': False, 'message': 'Original Invoice reference is required.'}, status=400)
                
            for det in details_data:
                qty = int(det.get('qty') or 0)
                if qty < 0:
                    return JsonResponse({'success': False, 'message': 'Returned quantity cannot be negative.'}, status=400)
                
                item_code = det.get('item_code')
                orig_item = InvoiceDet.objects.filter(tcode_id=ref_tcode, item_code_id=item_code).first()
                if not orig_item:
                    return JsonResponse({'success': False, 'message': f'Item {item_code} was not in original invoice.'}, status=400)
                    
                returned = InvoiceDet.objects.filter(
                    tcode__ref_tcode=ref_tcode,
                    tcode__dcode='3',
                    item_code_id=item_code
                ).aggregate(total_qty=Sum('qty'))
                
                prev_returned = returned['total_qty'] or 0
                if qty > (orig_item.qty - prev_returned):
                    return JsonResponse({
                        'success': False, 
                        'message': f'Returned quantity ({qty}) exceeds available return limit ({(orig_item.qty - prev_returned)}).'
                    }, status=400)
                    
            from decimal import Decimal
            with transaction.atomic():
                last_ret = InvoiceMst.objects.order_by('-tcode').first()
                tcode = (last_ret.tcode + 1) if last_ret else 1
                
                inv_date_str = master_data.get('inv_date')
                docno = None
                if inv_date_str:
                    from django.db import connection
                    with connection.cursor() as django_cursor:
                           raw_cursor = django_cursor.cursor.cursor
                           django_cursor.execute("SELECT GET_FISCALCODE(TO_DATE(%s, 'YYYY-MM-DD')) FROM DUAL", [inv_date_str])
                           fiscal_code = django_cursor.fetchone()[0]
                           
                           out_sqno = raw_cursor.var(str)
                           raw_cursor.callproc('GET_NEXT_SALES_TRNO', ['SRN', fiscal_code, out_sqno])
                           docno = out_sqno.getvalue()
                           
                master_obj = InvoiceMst.objects.create(
                    tcode=tcode,
                    docno=docno,
                    dcode='3',
                    inv_date=inv_date_str,
                    cstcode_id=master_data.get('cstcode'),
                    cmp_code_id=master_data.get('cmp_code'),
                    remarks=remarks,
                    ref_tcode=ref_tcode,
                    ent_by='admin'
                )
                
                det_objs = []
                for det in details_data:
                    qty = int(det.get('qty') or 0)
                    if qty == 0:
                        continue
                    det_objs.append(InvoiceDet(
                        tcode=master_obj,
                        item_code_id=det.get('item_code'),
                        qty=qty,
                        rate=Decimal(str(det.get('rate') or 0)),
                        dremarks=det.get('dremarks', '')
                    ))
                    
                InvoiceDet.objects.bulk_create(det_objs)
                
            return JsonResponse({'success': True, 'message': 'Sales Return saved successfully.', 'docno': docno})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
            
    elif request.method == 'PUT':
        try:
            data = json.loads(request.body)
            master_data = data.get('master', {})
            details_data = data.get('details', [])
            
            tcode = master_data.get('tcode')
            master_obj = InvoiceMst.objects.get(tcode=tcode, dcode='3')
            
            remarks = master_data.get('remarks')
            if not remarks or not remarks.strip():
                return JsonResponse({'success': False, 'message': 'Remarks are mandatory.'}, status=400)
                
            ref_tcode = master_obj.ref_tcode
            
            for det in details_data:
                qty = int(det.get('qty') or 0)
                if qty < 0:
                    return JsonResponse({'success': False, 'message': 'Returned quantity cannot be negative.'}, status=400)
                    
                item_code = det.get('item_code')
                orig_item = InvoiceDet.objects.filter(tcode_id=ref_tcode, item_code_id=item_code).first()
                if not orig_item:
                    return JsonResponse({'success': False, 'message': f'Item {item_code} was not in original invoice.'}, status=400)
                    
                returned = InvoiceDet.objects.filter(
                    tcode__ref_tcode=ref_tcode,
                    tcode__dcode='3',
                    item_code_id=item_code
                ).exclude(tcode_id=tcode).aggregate(total_qty=Sum('qty'))
                
                prev_returned = returned['total_qty'] or 0
                if qty > (orig_item.qty - prev_returned):
                    return JsonResponse({
                        'success': False, 
                        'message': f'Returned quantity ({qty}) exceeds available return limit ({(orig_item.qty - prev_returned)}).'
                    }, status=400)
                    
            from decimal import Decimal
            with transaction.atomic():
                master_obj.remarks = remarks
                master_obj.inv_date = master_data.get('inv_date')
                master_obj.modi_by = 'admin'
                master_obj.save()
                
                InvoiceDet.objects.filter(tcode=master_obj).delete()
                det_objs = []
                for det in details_data:
                    qty = int(det.get('qty') or 0)
                    if qty == 0:
                        continue
                    det_objs.append(InvoiceDet(
                        tcode=master_obj,
                        item_code_id=det.get('item_code'),
                        qty=qty,
                        rate=Decimal(str(det.get('rate') or 0)),
                        dremarks=det.get('dremarks', '')
                    ))
                InvoiceDet.objects.bulk_create(det_objs)
                
            return JsonResponse({'success': True, 'message': 'Sales Return updated successfully.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
            
    elif request.method == 'DELETE':
        try:
            tcode = request.GET.get('tcode')
            if not tcode:
                return JsonResponse({'success': False, 'message': 'tcode is required.'}, status=400)
            with transaction.atomic():
                InvoiceDet.objects.filter(tcode_id=tcode).delete()
                InvoiceMst.objects.filter(tcode=tcode, dcode='3').delete()
            return JsonResponse({'success': True, 'message': 'Sales Return deleted successfully.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
@csrf_exempt
def post_invoice_payments_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    import json
    from django.db import connection, transaction
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            tcodes = data.get('tcodes', [])
            if not tcodes:
                return JsonResponse({'success': False, 'message': 'No TCODEs provided.'})
            
            results = []
            errors = []
            for tcode in tcodes:
                try:
                    with transaction.atomic():
                        with connection.cursor() as django_cursor:
                            raw_cursor = django_cursor.cursor.cursor
                            out_srno = raw_cursor.var(int)
                            raw_cursor.callproc('KARAR.PRC_INVOICE_PAYMENT_POST', [int(tcode), out_srno])
                            srno_value = out_srno.getvalue()
                            
                            django_cursor.execute(
                                "UPDATE INVOICE_PAYMENT_MST SET GL_SRNO = %s, IS_POSTED = 'Y' WHERE TCODE = %s",
                                [srno_value, int(tcode)]
                            )
                            django_cursor.execute(
                                "UPDATE GL_VOUCH_MST SET IS_POSTED = 'Y' WHERE SRNO = %s",
                                [srno_value]
                            )
                            results.append({'tcode': tcode, 'gl_srno': srno_value})
                except Exception as e:
                    err_msg = str(e)
                    if "ORA-" in err_msg:
                        err_msg = err_msg.split('\n')[0]
                    errors.append({'tcode': tcode, 'error': err_msg})
            
            success_count = len(results)
            error_count = len(errors)
            if success_count > 0 and error_count == 0:
                return JsonResponse({'success': True, 'message': f'Successfully posted {success_count} payment(s) to GL.'})
            elif success_count > 0 and error_count > 0:
                return JsonResponse({'success': False, 'message': f'Posted {success_count} payment(s), but failed to post {error_count} payment(s). Errors: {errors}'})
            else:
                return JsonResponse({'success': False, 'message': f'Failed to post payments. Errors: {errors}'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)})
    return JsonResponse({'success': False, 'message': 'Method not allowed.'}, status=500)


@csrf_exempt
def invoice_payment_print_api(request):
    auth_error = require_sale_session(request)
    if auth_error:
        return auth_error
    from django.http import HttpResponse
    import io
    import datetime
    from django.db import connection
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from karar.views import number_to_words

    if request.method == 'GET':
        tcode = request.GET.get('tcode')
        if not tcode:
            return HttpResponse("Missing tcode", status=400)

        def escape_xml(val):
            if val is None:
                return ''
            return str(val).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        def fmt_date(d):
            if not d:
                return '-'
            if hasattr(d, 'strftime'):
                return d.strftime('%d-%b-%Y').upper()
            try:
                dt = datetime.datetime.strptime(str(d)[:10], '%Y-%m-%d')
                return dt.strftime('%d-%b-%Y').upper()
            except Exception:
                return str(d)

        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT a.TCODE, a.DOCNO, a.TDATE, a.CMP_CODE, NVL(e.CMP_NAME, 'KARAR TRADERS') AS CMP_NAME,
                           a.CSTCODE, NVL(c.NAME, '-') AS CUSTOMER_NAME,
                           a.CHEQUE_NO, a.CHEQUE_DATE, a.ACC_CODE, NVL(f.ACC_NAME, '-') AS ACC_NAME,
                           a.REMARKS, a.GL_SRNO,
                           d.INV_DATE, NVL(d.INVOICE_NO, d.DOCNO) AS INVOICE_NO, d.PO_NO,
                           NVL(b.BILL_AMOUNT, 0), NVL(b.PAID_AMOUNT, 0), NVL(b.TAX_AMOUNT, 0),
                           NVL(b.DED_AMOUNT, 0), NVL(b.RECEIVED_AMOUNT, 0)
                    FROM INVOICE_PAYMENT_MST a
                    LEFT JOIN INVOICE_PAYMENT_DET b ON a.TCODE = b.TCODE
                    LEFT JOIN CUSTOMER c ON a.CSTCODE = c.CSTCODE
                    LEFT JOIN INVOICE_MST d ON b.REF_TCODE = d.TCODE
                    LEFT JOIN COMPANIES e ON a.CMP_CODE = e.CMP_CODE
                    LEFT JOIN GL_COA f ON TO_CHAR(a.ACC_CODE) = TO_CHAR(f.ACC_CODE)
                    WHERE a.TCODE = %s
                    ORDER BY d.INV_DATE, b.REF_TCODE
                """, [tcode])
                rows = cursor.fetchall()

            if not rows:
                return HttpResponse("Invoice Payment Voucher not found", status=404)

            first = rows[0]
            master = {
                'tcode': first[0],
                'docno': first[1] or f"#{first[0]}",
                'tdate': first[2],
                'cmp_code': first[3],
                'cmp_name': (first[4] or 'KARAR TRADERS').upper(),
                'cstcode': first[5],
                'customer_name': first[6] or '-',
                'cheque_no': first[7] or '-',
                'cheque_date': first[8],
                'acc_code': first[9] or '',
                'acc_name': first[10] or '-',
                'remarks': first[11] or '-',
                'gl_srno': first[12],
            }

            acc_display = f"{master['acc_code']} - {master['acc_name']}" if master['acc_code'] and master['acc_name'] != '-' else (master['acc_name'] or '-')

            details = []
            tot_bill = 0
            tot_paid = 0
            tot_tax = 0
            tot_ded = 0
            tot_rcv = 0

            for r in rows:
                if r[14] is not None or r[16] is not None or r[20] is not None:
                    bill = float(r[16] or 0)
                    paid = float(r[17] or 0)
                    tax = float(r[18] or 0)
                    ded = float(r[19] or 0)
                    rcv = float(r[20] or 0)
                    tot_bill += bill
                    tot_paid += paid
                    tot_tax += tax
                    tot_ded += ded
                    tot_rcv += rcv
                    details.append({
                        'inv_date': r[13],
                        'invoice_no': r[14] or '-',
                        'po_no': r[15] or '-',
                        'bill_amount': bill,
                        'paid_amount': paid,
                        'tax_amount': tax,
                        'ded_amount': ded,
                        'received_amount': rcv
                    })

            response = HttpResponse(content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="invoice_received_voucher_{tcode}.pdf"'

            buffer = io.BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=36,
                leftMargin=36,
                topMargin=25,
                bottomMargin=25
            )
            elements = []
            styles = getSampleStyleSheet()

            # Palette matching Daily Expense Voucher
            navy_color = colors.HexColor('#1e3a8a')   # Deep Navy Blue
            gold_color = colors.HexColor('#b48c48')   # Elegant Gold Bar
            text_dark = colors.HexColor('#1e293b')    # Slate 800
            text_muted = colors.HexColor('#64748b')   # Slate 500
            border_color = colors.HexColor('#cbd5e1') # Slate 300
            light_bg = colors.HexColor('#f8fafc')     # Slate 50
            banner_bg = colors.HexColor('#f1f5f9')    # Slate 100

            company_style = ParagraphStyle(
                'CompName',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=11,
                leading=14,
                alignment=TA_CENTER,
                textColor=text_muted
            )

            title_style = ParagraphStyle(
                'VoucherTitle',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=18,
                leading=22,
                alignment=TA_CENTER,
                textColor=navy_color
            )

            meta_val_style = ParagraphStyle(
                'MetaVal',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=9,
                leading=13,
                textColor=text_dark
            )

            th_left_style = ParagraphStyle(
                'THL',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8,
                leading=11,
                alignment=TA_LEFT,
                textColor=colors.white
            )

            th_center_style = ParagraphStyle(
                'THC',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8,
                leading=11,
                alignment=TA_CENTER,
                textColor=colors.white
            )

            th_right_style = ParagraphStyle(
                'THR',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8,
                leading=11,
                alignment=TA_RIGHT,
                textColor=colors.white
            )

            td_style = ParagraphStyle(
                'TD',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8,
                leading=11,
                textColor=text_dark
            )

            td_center_style = ParagraphStyle(
                'TDC',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8,
                leading=11,
                alignment=TA_CENTER,
                textColor=text_dark
            )

            td_amt_style = ParagraphStyle(
                'TDAmt',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=8,
                leading=11,
                alignment=TA_RIGHT,
                textColor=text_dark
            )

            total_label_style = ParagraphStyle(
                'TotalLabel',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8.5,
                leading=12,
                alignment=TA_RIGHT,
                textColor=navy_color
            )

            total_val_style = ParagraphStyle(
                'TotalVal',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8.5,
                leading=12,
                alignment=TA_RIGHT,
                textColor=navy_color
            )

            words_style = ParagraphStyle(
                'Words',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=9,
                leading=13,
                textColor=text_dark
            )

            sig_style = ParagraphStyle(
                'Sig',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8.5,
                leading=12,
                alignment=TA_CENTER,
                textColor=navy_color
            )

            # 1. Top Gold Accent Bar
            elements.append(HRFlowable(width="100%", thickness=3.5, color=gold_color, spaceBefore=0, spaceAfter=14))

            # 2. Header
            elements.append(Paragraph(escape_xml(master['cmp_name']), company_style))
            elements.append(Spacer(1, 3))
            elements.append(Paragraph("INVOICE RECEIVED VOUCHER", title_style))
            elements.append(Spacer(1, 14))

            # 3. Metadata Table (2 Columns)
            tdate_str = fmt_date(master['tdate'])
            cheque_date_str = fmt_date(master['cheque_date'])
            gl_voucher_str = f"#{master['gl_srno']}" if master['gl_srno'] else "Unposted"

            meta_data = [
                [
                    Paragraph(f"<b><font color='#1e3a8a'>Customer Name:</font></b> &nbsp;{escape_xml(master['customer_name'])}", meta_val_style),
                    Paragraph(f"<b><font color='#1e3a8a'>Voucher No:</font></b> &nbsp;{escape_xml(master['docno'])}", meta_val_style)
                ],
                [
                    Paragraph(f"<b><font color='#1e3a8a'>Account Name:</font></b> &nbsp;{escape_xml(acc_display)}", meta_val_style),
                    Paragraph(f"<b><font color='#1e3a8a'>Date:</font></b> &nbsp;{tdate_str}", meta_val_style)
                ],
                [
                    Paragraph(f"<b><font color='#1e3a8a'>Cheque No:</font></b> &nbsp;{escape_xml(master['cheque_no'])}", meta_val_style),
                    Paragraph(f"<b><font color='#1e3a8a'>Cheque Date:</font></b> &nbsp;{cheque_date_str}", meta_val_style)
                ],
                [
                    Paragraph(f"<b><font color='#1e3a8a'>Remarks:</font></b> &nbsp;{escape_xml(master['remarks'])}", meta_val_style),
                    Paragraph(f"<b><font color='#1e3a8a'>GL Voucher #:</font></b> &nbsp;{gl_voucher_str}", meta_val_style)
                ]
            ]

            meta_table = Table(meta_data, colWidths=[310, 213])
            meta_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('LEFTPADDING', (0,0), (-1,-1), 0),
                ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 2.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
            ]))
            elements.append(meta_table)
            elements.append(Spacer(1, 14))

            # 4. Details Table (8 columns: 62 + 70 + 75 + 60 + 56 + 50 + 68 + 82 = 523 pt)
            table_headers = [
                Paragraph("<b>Invoice Date</b>", th_center_style),
                Paragraph("<b>Invoice No</b>", th_center_style),
                Paragraph("<b>PO No</b>", th_left_style),
                Paragraph("<b>Bill Amount</b>", th_right_style),
                Paragraph("<b>Already Paid</b>", th_right_style),
                Paragraph("<b>Tax</b>", th_right_style),
                Paragraph("<b>Other Deduction</b>", th_right_style),
                Paragraph("<b>Received Amt</b>", th_right_style),
            ]
            table_data = [table_headers]

            for d in details:
                table_data.append([
                    Paragraph(fmt_date(d['inv_date']), td_center_style),
                    Paragraph(escape_xml(d['invoice_no']), td_center_style),
                    Paragraph(escape_xml(d['po_no']), td_style),
                    Paragraph(f"{d['bill_amount']:,.2f}", td_amt_style),
                    Paragraph(f"{d['paid_amount']:,.2f}", td_amt_style),
                    Paragraph(f"{d['tax_amount']:,.2f}", td_amt_style),
                    Paragraph(f"{d['ded_amount']:,.2f}", td_amt_style),
                    Paragraph(f"{d['received_amount']:,.2f}", td_amt_style),
                ])

            table_data.append([
                Paragraph("<b>TOTAL</b>", total_label_style),
                "",
                "",
                Paragraph(f"<b>{tot_bill:,.2f}</b>", total_val_style),
                Paragraph(f"<b>{tot_paid:,.2f}</b>", total_val_style),
                Paragraph(f"<b>{tot_tax:,.2f}</b>", total_val_style),
                Paragraph(f"<b>{tot_ded:,.2f}</b>", total_val_style),
                Paragraph(f"<b>{tot_rcv:,.2f}</b>", total_val_style),
            ])

            col_widths = [62, 70, 75, 60, 56, 50, 68, 82]
            items_table = Table(table_data, colWidths=col_widths)
            table_style_list = [
                ('BACKGROUND', (0, 0), (-1, 0), navy_color),
                ('GRID', (0, 0), (-1, -1), 0.5, border_color),
                ('SPAN', (0, -1), (2, -1)),  # Span "TOTAL" across first 3 columns
                ('BACKGROUND', (0, -1), (-1, -1), light_bg),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 1), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ]

            for r_idx in range(1, len(details) + 1):
                if r_idx % 2 == 0:
                    table_style_list.append(('BACKGROUND', (0, r_idx), (-1, r_idx), light_bg))

            items_table.setStyle(TableStyle(table_style_list))
            elements.append(items_table)
            elements.append(Spacer(1, 10))

            # 5. Total in Words Strip
            total_in_words = number_to_words(tot_rcv)
            if not total_in_words.strip().lower().endswith('only'):
                total_in_words = f"{total_in_words} Only"

            words_banner_data = [[
                Paragraph(f"<b><font color='#1e3a8a'>Total In Words:</font></b> &nbsp;<i>{escape_xml(total_in_words)}</i>", words_style)
            ]]
            words_table = Table(words_banner_data, colWidths=[523])
            words_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), banner_bg),
                ('BOX', (0,0), (-1,-1), 0.5, border_color),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 4.5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
            ]))
            elements.append(words_table)
            elements.append(Spacer(1, 35))

            # 6. Signature block (4 Signatures)
            sig_line = "______________________"
            sig_data = [
                [sig_line, sig_line, sig_line, sig_line],
                [
                    Paragraph("<b>Prepared By</b>", sig_style),
                    Paragraph("<b>Checked By</b>", sig_style),
                    Paragraph("<b>Authorized Signatory</b>", sig_style),
                    Paragraph("<b>Customer Signature</b>", sig_style)
                ]
            ]
            sig_table = Table(sig_data, colWidths=[130, 131, 131, 131])
            sig_table.setStyle(TableStyle([
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('TEXTCOLOR', (0,0), (-1,0), text_dark),
                ('BOTTOMPADDING', (0,0), (-1,0), 3),
                ('TOPPADDING', (0,1), (-1,1), 2),
            ]))
            elements.append(sig_table)
            elements.append(Spacer(1, 8))

            # 7. Separator & Office Address Footer
            elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor('#cbd5e1'), spaceBefore=2, spaceAfter=8))
            footer_addr_style = ParagraphStyle(
                'FooterAddr',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=8.5,
                leading=12,
                alignment=TA_CENTER,
                textColor=colors.HexColor('#0f172a')
            )
            elements.append(Paragraph("Office Address: At ONB, B-9/C, 2nd Floor, Block-B, Mehersons Estate, Talpur Road, Karachi-Pakistan.", footer_addr_style))
            elements.append(Paragraph("Tel: +92-213-2460500 Mob: +92-300-2346993", footer_addr_style))

            doc.build(elements)
            pdf = buffer.getvalue()
            buffer.close()
            response.write(pdf)
            return response
        except Exception as e:
            return HttpResponse(f"Error generating PDF: {str(e)}", status=500)

    return HttpResponse("Method not allowed", status=405)

