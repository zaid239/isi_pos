import { API_BASE } from '../config';
import React, { useState, useEffect, useRef } from 'react';
import { 
  Form, Input, InputNumber, Select, Button, message, 
  Table, Space, Tag, Modal, Divider, Checkbox, DatePicker, Popconfirm
} from 'antd';
import PageLayout from '../components/PageLayout';
import FormLayout from '../components/FormLayout';
import { 
  SearchOutlined, EditOutlined, PlusOutlined, 
  DeleteOutlined, FileTextOutlined, ReloadOutlined,
  ArrowLeftOutlined, SaveOutlined, ShoppingCartOutlined, PrinterOutlined,
  ExclamationCircleOutlined
} from '@ant-design/icons';
import dayjs from 'dayjs';

const { Option } = Select;
const { TextArea } = Input;

export default function SalesInvoice({ user }) {
  const defaultDcode = '2';
  const docTypeName = 'Invoice';
  const [form] = Form.useForm();
  
  const [invoices, setInvoices] = useState([]);
  
  // Lookups
  const [companies, setCompanies] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [directors, setDirectors] = useState([]);
  const [paymentTerms, setPaymentTerms] = useState([]);
  const [items, setItems] = useState([]);
  const [pendingDcs, setPendingDcs] = useState([]);
  
  const [fetching, setFetching] = useState(false);
  const [saving, setSaving] = useState(false);
  const submittingRef = useRef(false);
  
  const [isFormView, setIsFormView] = useState(false);
  const [editingTcode, setEditingTcode] = useState(null);
  
  const [details, setDetails] = useState([]);
  const [isGstEnabled, setIsGstEnabled] = useState(false);

  // Track if invoice number is locked (generated or loaded from DB)
  const [invoiceNoLocked, setInvoiceNoLocked] = useState(false);

  const [activeRowKey, setActiveRowKey] = useState(null);
  const [isItemModalOpen, setIsItemModalOpen] = useState(false);
  const [itemSearchQuery, setItemSearchQuery] = useState('');

  // Watch invoice_no and metadata to display the badge
  const watchedInvoiceNo = Form.useWatch('invoice_no', form);
  const watchedInvoiceDate = Form.useWatch('invoice_date', form);
  const watchedInvoiceRemarks = Form.useWatch('invoice_remarks', form);

  // Filters
  const [filterFromDate, setFilterFromDate] = useState(null);
  const [filterToDate, setFilterToDate] = useState(null);
  const [filterCompany, setFilterCompany] = useState(null);
  const [filterCustomer, setFilterCustomer] = useState(null);
  const [filterInvoiceNo, setFilterInvoiceNo] = useState('');
  const [filterPoNo, setFilterPoNo] = useState('');
  const [filterChallanNo, setFilterChallanNo] = useState('');
  const [filterDocType, setFilterDocType] = useState(defaultDcode);

  useEffect(() => {
    fetchLookups();
  }, []);

  useEffect(() => {
    setFilterDocType(defaultDcode);
    setInvoices([]);
    setIsFormView(false);
    setEditingTcode(null);
    setFilterCustomer(null);
  }, [defaultDcode]);



  const fetchLookups = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/sales/lookup/`, { cache: 'no-store', credentials: 'include' });
      const data = await response.json();
      if (data.success) {
        setCompanies(data.companies || []);
        setCustomers(data.customers || []);
        setDirectors(data.directors || []);
        setPaymentTerms(data.payment_terms || []);
        setItems(data.items || []);
        setPendingDcs(data.pending_dcs || []);
      }
    } catch (error) {
      console.error('Error fetching lookups:', error);
    }
  };

  const fetchInvoices = async () => {
    setFetching(true);
    try {
      let url = `${API_BASE}/api/sales/invoice/?`;
      const params = new URLSearchParams();
      if (filterFromDate) {
        params.append('start_date', filterFromDate.format('YYYY-MM-DD'));
      }
      if (filterToDate) {
        params.append('end_date', filterToDate.format('YYYY-MM-DD'));
      }
      if (filterCompany) {
        params.append('cmp_code', filterCompany);
      }
      if (filterCustomer) {
        params.append('cstcode', filterCustomer);
      }
      if (filterInvoiceNo) {
        params.append('invoice_no', filterInvoiceNo);
      }
      if (filterPoNo) {
        params.append('po_no', filterPoNo);
      }
      if (filterChallanNo) {
        params.append('challan_no', filterChallanNo);
      }
      if (filterDocType) {
        params.append('dcode', filterDocType);
        if (filterDocType === '1') {
          params.append('pending_only', '1');
        }
      }
      const response = await fetch(url + params.toString(), { cache: 'no-store', credentials: 'include' });
      const data = await response.json();
      if (data.success) {
        setInvoices((data.invoices || []).filter(item => item.dc_flag !== 'Y'));
      } else {
        message.error('Failed to fetch invoices');
      }
    } catch (error) {
      console.error('Error fetching invoices:', error);
    } finally {
      setFetching(false);
    }
  };

  const handleCompanyChange = (cmpCode) => {
    const comp = companies.find(c => c.cmp_code === cmpCode);
    const enabled = comp && comp.cmp_code === 1 && comp.tax_flag === 'Y';
    setIsGstEnabled(enabled);
    if (!enabled) {
      // Clear GST in details if disabled
      const newDetails = details.map(d => ({ ...d, gst: 0, add_tax: 0 }));
      setDetails(newDetails);
      form.setFieldsValue({ gstbase_flag: false });
    } else {
      form.setFieldsValue({ gstbase_flag: true });
    }
  };


  const handleCreateNew = () => {
    fetchLookups();
    setEditingTcode(null);
    setDetails([]);
    setIsGstEnabled(false);
    setInvoiceNoLocked(false);

    form.resetFields();
    form.setFieldsValue({
      inv_date: dayjs(),
      opening_flag: false,
      gstbase_flag: false,
      docno: 'Auto',
      challan_no: 'Auto',
      dcode: defaultDcode || '1',
      ref_tcode: null,
      delivery_charges: 0,
      discount: 0,
    });
    setIsFormView(true);
  };

  const handleEdit = async (record) => {
    setFetching(true);
    try {
      const response = await fetch(`${API_BASE}/api/sales/invoice/?tcode=${record.tcode}`, { cache: 'no-store', credentials: 'include' });
      const data = await response.json();
      if (data.success) {
        const master = data.master;
        
        const comp = companies.find(c => c.cmp_code === master.cmp_code);
        const enabled = comp && comp.cmp_code === 1 && comp.tax_flag === 'Y';
        setIsGstEnabled(enabled);

        if (defaultDcode === '2' && (master.dcode || '').trim() === '1' && master.is_invoiced) {
          message.warning('This Delivery Challan has already been invoiced and cannot be invoiced again.');
          setFetching(false);
          return;
        }

        const isConvertingFromDc = defaultDcode === '2' && (master.dcode || '').trim() === '1' && !master.is_invoiced && !(master.invoice_no && master.invoice_no.trim());

        if (defaultDcode === '2' && master.dc_flag === 'Y') {
          message.error("This Delivery Challan is marked as 'ONLY DC' and cannot be converted to an Invoice.");
          setFetching(false);
          return;
        }

        form.setFieldsValue({
          docno: isConvertingFromDc ? 'Auto' : (master.docno || 'Auto'),
          dcode: defaultDcode,
          inv_date: master.inv_date ? dayjs(master.inv_date) : dayjs(),
          cmp_code: master.cmp_code,
          dir_code: master.dir_code,
          cstcode: master.cstcode,
          pcode: master.pcode,
          invoice_no: isConvertingFromDc ? null : (master.invoice_no || null),
          po_no: master.po_no,
          challan_no: master.challan_no || 'Auto',
          remarks: master.remarks || (isConvertingFromDc ? `Created from DC No: ${master.invoice_no || ''}` : ''),
          delivery_charges: Number(master.delivery_charges || 0),
          discount: Number(master.discount || 0),
          gstbase_flag: master.gstbase_flag === 'Y',
          opening_flag: master.opening_flag === 'Y',
          invoice_date: master.invoice_date ? dayjs(master.invoice_date) : null,
          invoice_remarks: master.invoice_remarks || '',
          invoice_generated_date: master.invoice_generated_date ? dayjs(master.invoice_generated_date) : null,
          ref_tcode: isConvertingFromDc ? master.tcode : (master.ref_tcode || null),
          dc_date: master.dc_date ? dayjs(master.dc_date).format('DD-MMM-YYYY').toUpperCase() : '---',
          dc_remarks: master.dc_remarks || '---',
        });

        if (isConvertingFromDc) {
          setEditingTcode(null);
          setInvoiceNoLocked(false);
        } else {
          setEditingTcode(master.tcode);
          setInvoiceNoLocked(!!master.invoice_no);
        }
        
        // Populate details and add unique id for React keys
        const dets = (data.details || []).map((d, i) => ({
          ...d,
          key: `row_${i}_${Date.now()}`
        }));
        setDetails(dets);
        setIsFormView(true);
      } else {
        message.error('Failed to load invoice details');
      }
    } catch (error) {
      message.error('Network error fetching invoice');
    } finally {
      setFetching(false);
    }
  };

  const handleDelete = async (record) => {
    try {
      const response = await fetch(`${API_BASE}/api/sales/invoice/`, {
          credentials: 'include',
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tcode: record.tcode }),
      });
      const data = await response.json();
      if (response.ok && data.success) {
        message.success('Invoice deleted successfully!');
        fetchInvoices();
      } else {
        message.error(data.message || 'Failed to delete invoice.');
      }
    } catch (error) {
      console.error(error);
      message.error('Network error while deleting.');
    }
  };

  const handleAddItem = () => {
    setDetails([
      ...details,
      {
        key: `new_${Date.now()}`,
        item_code: undefined,
        tmp_qty: 0,
        qty: 0,
        rate: 0,
        gst: 0,
        add_tax: 0,
        dremarks: ''
      }
    ]);
  };

  const handleRemoveItem = (key) => {
    setDetails(details.filter(d => d.key !== key));
  };

  const handleDetailChange = (key, field, value) => {
    const newDetails = details.map(d => {
      if (d.key === key) {
        if (field === 'item_code') {
          const selectedItem = items.find(i => i.item_code === value);
          const ledgerStock = selectedItem ? (selectedItem.ledger_stock || 0) : 0;
          return { ...d, item_code: value, tmp_qty: ledgerStock };
        }
        return { ...d, [field]: value };
      }
      return d;
    });
    setDetails(newDetails);
  };

  const onFinish = async (values, confirmedSync = false) => {
    if (submittingRef.current || saving) {
      return;
    }
    if (details.length === 0) {
      message.warning('Please add at least one item detail');
      return;
    }

    submittingRef.current = true;
    setSaving(true);
    try {
      let manualNo = values.invoice_no;
      if (!manualNo || manualNo === 'Auto') {
        // Automatically generate invoice number on save/post
        const invDate = values.inv_date;
        const cmpCode = values.cmp_code;
        if (!invDate) {
          message.warning('Please select an Invoice Date first.');
          submittingRef.current = false;
          setSaving(false);
          return;
        }
        if (!cmpCode) {
          message.warning('Please select a Company first.');
          submittingRef.current = false;
          setSaving(false);
          return;
        }

        try {
          const genRes = await fetch(`${API_BASE}/api/sales/invoice/generate-no/`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              inv_date: invDate.format('YYYY-MM-DD'),
              cmp_code: cmpCode,
              ref_tcode: values.ref_tcode || null,
              challan_no: values.challan_no && values.challan_no !== 'Auto' ? values.challan_no : null,
            }),
          });
          const genData = await genRes.json();
          if (genData.success) {
            manualNo = genData.manual_no;
            form.setFieldsValue({ invoice_no: manualNo });
            setInvoiceNoLocked(true);
            message.success(`Invoice No automatically generated: ${manualNo}`);
          } else {
            message.error(genData.message || 'Failed to automatically generate invoice number.');
            submittingRef.current = false;
            setSaving(false);
            return;
          }
        } catch (err) {
          console.error(err);
          message.error('Network error while generating invoice number.');
          submittingRef.current = false;
          setSaving(false);
          return;
        }
      }

      const masterPayload = {
        ...values,
        dcode: defaultDcode,
        docno: values.docno === 'Auto' ? null : values.docno,
        invoice_no: manualNo,
        challan_no: values.challan_no === 'Auto' ? null : values.challan_no,
        inv_date: values.inv_date ? values.inv_date.format('YYYY-MM-DD') : null,
        gstbase_flag: values.gstbase_flag ? 'Y' : 'N',
        opening_flag: values.opening_flag ? 'Y' : 'N',
      };

      if (editingTcode) {
        masterPayload.tcode = editingTcode;
      }

      const method = editingTcode ? 'PUT' : 'POST';
      
      const response = await fetch(`${API_BASE}/api/sales/invoice/`, {
        credentials: 'include',
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          master: masterPayload,
          details: details,
          sync_gl_dc: true
        }),
      });

      const data = await response.json();
      if (response.ok && data.success) {
        message.success(editingTcode ? `${data.message || docTypeName + ' updated successfully!'}` : `${docTypeName} created successfully! Doc No: ${data.docno || ''}`);
        
        if (!editingTcode) {
          form.setFieldsValue({ 
            docno: data.docno,
            challan_no: data.challan_no
          });
          setEditingTcode(data.tcode);
        }
        
        fetchInvoices();
      } else {
        message.error(data.message || `Failed to save ${docTypeName.toLowerCase()}.`);
      }
    } catch (error) {
      console.error(error);
      message.error('Network error while saving.');
    } finally {
      submittingRef.current = false;
      setSaving(false);
    }
  };

  const handlePrint = () => {
    if (editingTcode) {
      window.open(`${API_BASE}/api/sales/invoice/print/?tcode=${editingTcode}`, '_blank');
    }
  };

  const handlePrintDummy = () => {
    if (editingTcode) {
      window.open(`${API_BASE}/api/sales/invoice/print/?tcode=${editingTcode}&is_dummy=true`, '_blank');
    }
  };



  // Compute values for a row
  const getRowCalculations = (row) => {
    const qty = Number(row.qty) || 0;
    const rate = Number(row.rate) || 0;
    const baseAmt = qty * rate;
    
    let gstAmt = 0;
    let addTaxAmt = 0;

    if (isGstEnabled) {
      const gstPct = Number(row.gst) || 0;
      const addTaxPct = Number(row.add_tax) || 0;
      gstAmt = Math.round(baseAmt * (gstPct / 100));
      addTaxAmt = Math.round(baseAmt * (addTaxPct / 100));
    }
    
    const netAmt = baseAmt + gstAmt + addTaxAmt;
    
    return { baseAmt, gstAmt, addTaxAmt, netAmt };
  };

  const totalQty = details.reduce((sum, row) => sum + (Number(row.qty) || 0), 0);
  const totalAmount = details.reduce((sum, row) => sum + getRowCalculations(row).netAmt, 0);
  const discountWatcher = Form.useWatch('discount', form) || 0;
  const deliveryChargesWatcher = Form.useWatch('delivery_charges', form) || 0;
  const netFinalInvoiceAmount = Math.max(0, totalAmount - (Number(discountWatcher) || 0) + (Number(deliveryChargesWatcher) || 0));

  // List columns
  const listColumns = [
    { 
      title: 'Tcode', 
      dataIndex: 'tcode', 
      key: 'tcode',
      sorter: (a, b) => a.tcode - b.tcode,
      render: (t, record) => (
        <Button 
          type="link" 
          onClick={() => handleEdit(record)} 
          className="p-0 text-purple-800 hover:text-purple-950 font-medium h-auto"
        >
          {t}
        </Button>
      ) 
    },
    { 
      title: 'Date', 
      dataIndex: 'inv_date', 
      key: 'date', 
      sorter: (a, b) => dayjs(a.inv_date || 0).unix() - dayjs(b.inv_date || 0).unix(),
      defaultSortOrder: 'descend',
      render: text => text ? dayjs(text).format('DD-MMM-YYYY').toUpperCase() : '-' 
    },
    { title: 'Invoice No.', dataIndex: 'invoice_no', key: 'invoice_no', render: t => t || '---' },
    { title: 'PO No', dataIndex: 'po_no', key: 'po_no', render: t => t || '---' },
    { title: 'Challan No', dataIndex: 'challan_no', key: 'challan_no', render: t => t || '---' },
    { title: 'Company', dataIndex: 'cmp_code__cmp_name', key: 'company' },
    { title: 'Name', dataIndex: 'cstcode__name', key: 'name', className: 'text-slate-700 font-medium' },
    { title: 'Amount', dataIndex: 'amount', key: 'amount', align: 'right', render: val => val ? Math.round(Number(val)).toLocaleString() : '0' },
    { 
      title: 'Invoice', 
      key: 'invoice', 
      render: (_, record) => {
        const d = (record.dcode || '').trim();
        const hasInvoiceNo = !!(record.invoice_no && record.invoice_no.trim());
        return (d === '2' || hasInvoiceNo || record.is_invoiced) ? 'Yes' : 'No';
      }
    },
    {
      title: 'Actions',
      key: 'actions',
      align: 'center',
      render: (_, record) => (
        <Space size="middle">
          <Button type="text" size="small" icon={<PrinterOutlined className="text-emerald-600"/>} onClick={() => window.open(`${API_BASE}/api/sales/invoice/print/?tcode=${record.tcode}`, '_blank')} title="Print Invoice" className="p-0 h-auto" />
          <Button type="text" size="small" icon={<FileTextOutlined className="text-blue-600"/>} onClick={() => window.open(`${API_BASE}/api/sales/invoice/dc-print/?tcode=${record.tcode}`, '_blank')} title="Print DC" className="p-0 h-auto" />
          <Button 
            type="text" 
            size="small" 
            icon={<EditOutlined className="text-indigo-600" />} 
            onClick={() => handleEdit(record)} 
            title="Edit Invoice"
            className="p-0 h-auto" 
          />
          {(user?.ulevl === 'D' || user?.ulevl === 'A') && (
            <Popconfirm
              title="Are you sure you want to delete this invoice?"
              onConfirm={() => handleDelete(record)}
              okText="Yes"
              cancelText="No"
            >
              <Button type="text" size="small" icon={<DeleteOutlined className="text-rose-600"/>} title="Delete Invoice" className="p-0 h-auto" />
            </Popconfirm>
          )}
        </Space>
      )
    }
  ];

  return (
      <>
        {!isFormView ? (
          <PageLayout
            title={defaultDcode === '1' ? "Delivery Challans" : "Sales Invoices"}
            description={defaultDcode === '1' ? "Manage delivery challans and shipments" : "Manage sales invoices and billing"}
            onCreate={defaultDcode === '1' ? handleCreateNew : undefined}
            createLabel={defaultDcode === '1' ? "New Delivery Challan" : "New Invoice"}
            filters={
              <>
                <div>
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">From Date</div>
                  <DatePicker 
                    value={filterFromDate} 
                    onChange={setFilterFromDate} 
                    className="w-full rounded-md" 
                    size="small"
                    format="DD-MMM-YYYY"
                    placeholder="Select date"
                  />
                </div>
                <div>
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">To Date</div>
                  <DatePicker 
                    value={filterToDate} 
                    onChange={setFilterToDate} 
                    className="w-full rounded-md" 
                    size="small"
                    format="DD-MMM-YYYY"
                    placeholder="Select date"
                  />
                </div>
                <div className="w-[200px]">
                   <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Company</div>
                   <Select 
                     placeholder="All Companies" 
                     allowClear 
                     size="small"
                     className="w-full"
                     value={filterCompany}
                     onChange={setFilterCompany}
                     showSearch optionFilterProp="children"
                   >
                     {companies.map(c => <Option key={c.cmp_code} value={c.cmp_code}>{c.cmp_name}</Option>)}
                   </Select>
                 </div>
                 <div className="w-[320px]">
                   <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Customer</div>
                   <Select 
                     placeholder="All Customers" 
                     allowClear 
                     size="small"
                     className="w-full"
                     value={filterCustomer}
                     onChange={setFilterCustomer}
                     showSearch optionFilterProp="children"
                   >
                     {customers.map(c => <Option key={c.cstcode} value={c.cstcode}>{c.name}</Option>)}
                   </Select>
                 </div>
                <div className="w-[110px]">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Doc Type</div>
                  <Select 
                    size="small"
                    className="w-full rounded-md"
                    placeholder="Select Doc Type"
                    value={filterDocType}
                    onChange={setFilterDocType}
                    allowClear
                  >
                    <Option value="1">DC</Option>
                    <Option value="2">Invoice</Option>
                  </Select>
                </div>
                <div className="w-[120px]">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">{defaultDcode === '1' ? 'DC No' : 'Invoice No'}</div>
                  <Input 
                    placeholder={defaultDcode === '1' ? 'DC #' : 'Invoice #'} 
                    size="small" 
                    value={filterInvoiceNo} 
                    onChange={e => setFilterInvoiceNo(e.target.value)} 
                    allowClear
                  />
                </div>
                <div className="w-[120px]">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">PO No</div>
                  <Input 
                    placeholder="PO #" 
                    size="small" 
                    value={filterPoNo} 
                    onChange={e => setFilterPoNo(e.target.value)} 
                    allowClear
                  />
                </div>
                <div className="w-[120px]">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Challan No</div>
                  <Input 
                    placeholder="Challan #" 
                    size="small" 
                    value={filterChallanNo} 
                    onChange={e => setFilterChallanNo(e.target.value)} 
                    allowClear
                  />
                </div>
                <Button type="primary" size="small" icon={<SearchOutlined />} onClick={fetchInvoices}>
                  Search
                </Button>
              </>
            }
          >
            <div className="flex justify-between items-center mb-2 px-1">
              <span className="text-xs font-semibold text-slate-500">
                Total Records Found: <span className="text-indigo-600 font-bold">{invoices.length}</span>
              </span>
            </div>
            <Table 
              columns={listColumns} 
              dataSource={invoices} 
              rowKey="tcode" 
              loading={fetching}
              size="small"
              bordered
              scroll={{ x: 1000 }}
              pagination={{ pageSize: 12 }}
              summary={() => {
                const totalSum = invoices.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
                return (
                  <Table.Summary.Row className="bg-slate-50 font-bold">
                    <Table.Summary.Cell index={0} colSpan={7} className="text-right">Total Invoice Amount:</Table.Summary.Cell>
                    <Table.Summary.Cell index={1} align="right" className="text-slate-800">
                      {totalSum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={2} colSpan={2} />
                  </Table.Summary.Row>
                );
              }}
            />
          </PageLayout>
        ) : (
          <FormLayout
            title={editingTcode ? `Edit Karar ${docTypeName} #${editingTcode}` : `New Karar ${docTypeName}`}
            onBack={() => { setIsFormView(false); setEditingTcode(null); }}
            footer={
              <>
                <Button size="small" onClick={() => { setIsFormView(false); setEditingTcode(null); }}>Cancel</Button>
                {editingTcode && (
                  <>
                    <Button size="small" onClick={handlePrint} icon={<PrinterOutlined />}>Print Invoice</Button>
                    <Button 
                      size="small" 
                      onClick={handlePrintDummy} 
                      icon={<PrinterOutlined />}
                      className="border-amber-400 text-amber-800 bg-amber-50 hover:bg-amber-100 font-medium"
                    >
                      Dummy Invoice
                    </Button>
                    <Button size="small" onClick={() => window.open(`${API_BASE}/api/sales/invoice/dc-print/?tcode=${editingTcode}`, '_blank')} icon={<FileTextOutlined />}>Print DC</Button>
                    <Button 
                      size="small" 
                      onClick={() => window.open(`${API_BASE}/api/sales/invoice/dc-print/?tcode=${editingTcode}&is_dummy=true`, '_blank')} 
                      icon={<FileTextOutlined />}
                      className="border-amber-400 text-amber-800 bg-amber-50 hover:bg-amber-100 font-medium"
                    >
                      Dummy DC
                    </Button>
                  </>
                )}
                <Button type="primary" size="small" onClick={() => form.submit()} loading={saving} icon={<SaveOutlined />}>
                  {editingTcode ? 'Update Invoice' : (defaultDcode === '1' ? 'Save Challan' : 'Generate Invoice')}
                </Button>
              </>
            }
          >
              <Form form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
                {/* Master Fields */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                   <div>
                     <Form.Item name="docno" label={<span className="text-xs font-medium text-slate-700">Doc#</span>} className="mb-2">
                       <Input disabled size="small" className="bg-slate-50" />
                     </Form.Item>
                   </div>
                   {defaultDcode === '2' && (
                     <div>
                       <Form.Item name="ref_tcode" label={<span className="text-xs font-medium text-slate-700">Ref Code</span>} className="mb-2">
                         <Input disabled size="small" className="bg-slate-50" placeholder="No Referenced DC" />
                       </Form.Item>
                     </div>
                   )}
                  <div>
                    <Form.Item name="inv_date" label={<span className="text-xs font-medium text-slate-700">{docTypeName} Date <span className="text-red-500">*</span></span>} rules={[{ required: true }]} className="mb-2">
                      <DatePicker size="small" className="w-full" format="DD-MMM-YYYY" />
                    </Form.Item>
                  </div>
                  <div>
                    <Form.Item name="cmp_code" label={<span className="text-xs font-medium text-slate-700">Company <span className="text-red-500">*</span></span>} rules={[{ required: true }]} className="mb-2">
                      <Select size="small" placeholder="Select Company" onChange={handleCompanyChange} showSearch optionFilterProp="children" disabled={!!editingTcode}>
                        {companies.map(c => <Option key={c.cmp_code} value={c.cmp_code}>{c.cmp_name}</Option>)}
                      </Select>
                    </Form.Item>
                  </div>
                  <div>
                    <Form.Item name="dcode" label={<span className="text-xs font-medium text-slate-700">Doc Type <span className="text-red-500">*</span></span>} rules={[{ required: true }]} className="mb-2">
                      <Select size="small" placeholder="Doc Type" disabled={!!defaultDcode}>
                        <Option value="1">DC</Option>
                        <Option value="2">Invoice</Option>
                      </Select>
                    </Form.Item>
                  </div>
                  <div>
                    <Form.Item name="dir_code" label={<span className="text-xs font-medium text-slate-700">Director / Executive</span>} className="mb-2">
                      <Select size="small" placeholder="Select Director" showSearch optionFilterProp="children" allowClear>
                        {directors.map(d => <Option key={d.code} value={d.code}>{d.dir_name}</Option>)}
                      </Select>
                    </Form.Item>
                  </div>
                  <div className="md:col-span-2">
                    <Form.Item name="cstcode" label={<span className="text-xs font-medium text-slate-700">Customer <span className="text-red-500">*</span></span>} rules={[{ required: true }]} className="mb-2">
                      <Select size="small" placeholder="Select Customer" showSearch optionFilterProp="children" allowClear>
                        {customers.map(c => <Option key={c.cstcode} value={c.cstcode}>{c.name}</Option>)}
                      </Select>
                    </Form.Item>
                  </div>
                  <div>
                    <Form.Item name="pcode" label={<span className="text-xs font-medium text-slate-700">Payment Terms</span>} className="mb-2">
                      <Select size="small" placeholder="Terms" showSearch optionFilterProp="children" allowClear>
                        {paymentTerms.map(p => <Option key={p.pcode} value={p.pcode}>{p.descr}</Option>)}
                      </Select>
                    </Form.Item>
                  </div>
                  <div>
                    <Form.Item name="invoice_no" label={<span className="text-xs font-medium text-slate-700">Invoice No</span>} className="mb-2">
                      {invoiceNoLocked ? (
                        <div className="flex items-center gap-2 h-[24px] flex-wrap">
                          <span className="inline-flex items-center gap-1.5 px-3 py-0.5 rounded-full text-xs font-bold bg-emerald-50 border border-emerald-300 text-emerald-700 tracking-wide shadow-sm" title={watchedInvoiceRemarks}>
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block"></span>
                            {watchedInvoiceNo}
                          </span>
                          {watchedInvoiceDate && (
                            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 border border-blue-200 text-blue-700" title={`Remarks: ${watchedInvoiceRemarks || 'None'}`}>
                              Gen Date: {dayjs(watchedInvoiceDate).format('DD-MMM-YYYY')}
                            </span>
                          )}
                        </div>
                      ) : (
                        <Input size="small" placeholder="Auto" disabled className="bg-slate-50" />
                      )}
                    </Form.Item>
                  </div>
                  <div className="md:col-span-2">
                    <Form.Item name="po_no" label={<span className="text-xs font-medium text-slate-700">PO No</span>} className="mb-2">
                      <Input size="small" placeholder="Ref#" />
                    </Form.Item>
                  </div>
                  <div className="md:col-span-1">
                    <Form.Item name="challan_no" label={<span className="text-xs font-medium text-slate-700">Challan No</span>} className="mb-2">
                      <Input size="small" disabled className="bg-slate-50" placeholder="Auto" />
                    </Form.Item>
                  </div>
                  {editingTcode && (
                    <>
                      <div className="md:col-span-2">
                        <Form.Item name="dc_date" label={<span className="text-xs font-medium text-slate-700">DC Date</span>} className="mb-2">
                          <Input size="small" disabled className="bg-slate-50 text-slate-600 font-semibold" />
                        </Form.Item>
                      </div>
                      <div className="md:col-span-4">
                        <Form.Item name="dc_remarks" label={<span className="text-xs font-medium text-slate-700">DC Remarks</span>} className="mb-2">
                          <TextArea autoSize={{ minRows: 1, maxRows: 4 }} disabled className="bg-slate-50 text-slate-600 font-semibold whitespace-pre-wrap" />
                        </Form.Item>
                      </div>
                    </>
                  )}
                  <div className="flex items-end gap-3 pb-2 md:col-span-2">
                    <Form.Item name="gstbase_flag" valuePropName="checked" className="mb-0">
                      <Checkbox className="text-[11px] font-medium text-slate-600" disabled={!isGstEnabled}>GST APPLIED</Checkbox>
                    </Form.Item>
                    <Form.Item name="opening_flag" valuePropName="checked" className="mb-0">
                      <Checkbox className="text-[11px] font-medium text-slate-600">OPENING</Checkbox>
                    </Form.Item>
                  </div>
                </div>

                {/* Line Items Section */}
                <div className="flex justify-between items-center mb-2 mt-4">
                  <div className="text-sm font-medium text-slate-700">Line Items</div>
                  <Button onClick={handleAddItem} size="small" icon={<PlusOutlined />} className="text-xs">
                    Add Item
                  </Button>
                </div>

                <div className="border border-slate-200 rounded-lg overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead>
                        <tr className="bg-slate-100">
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[340px]">ITEM NAME</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[200px]">REMARKS</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[80px]">TMP QTY</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[80px]">QTY</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[140px]">RATE</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[100px]">BASE AMT</th>
                          {isGstEnabled && (
                            <>
                              <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[80px]">GST%</th>
                              <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[100px]">TAX AMT</th>
                            </>
                          )}
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[120px]">NET AMOUNT</th>
                          <th className="px-3 py-2 text-[10px] font-medium text-slate-600 uppercase tracking-wide w-[60px] text-center">ACTION</th>
                        </tr>
                      </thead>
                      <tbody>
                        {details.map((row) => {
                          const calcs = getRowCalculations(row);
                          return (
                            <tr key={row.key} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                              <td className="p-2">
                                <Input
                                  size="small"
                                  placeholder="Click to select item..."
                                  value={(() => { const itm = items.find(i => String(i.item_code) === String(row.item_code)); return itm?.item_name2 || itm?.item_name || row.item_code__item_name2 || row.item_code__item_name || ''; })()}
                                  readOnly
                                  onClick={() => {
                                    setActiveRowKey(row.key);
                                    setIsItemModalOpen(true);
                                    setItemSearchQuery('');
                                  }}
                                  suffix={<SearchOutlined className="text-slate-400 cursor-pointer" />}
                                  className="cursor-pointer bg-white hover:border-blue-400"
                                />
                              </td>
                              <td className="p-2">
                                <Input
                                  size="small"
                                  placeholder="Item remarks..."
                                  value={row.dremarks || ''}
                                  onChange={e => handleDetailChange(row.key, 'dremarks', e.target.value)}
                                  className="text-xs"
                                />
                              </td>
                              <td className="p-2">
                                <Input 
                                  size="small" 
                                  type="number" 
                                  value={row.tmp_qty} 
                                  onChange={e => handleDetailChange(row.key, 'tmp_qty', e.target.value)} 
                                  disabled={!!editingTcode}
                                  className={editingTcode ? "bg-slate-50 text-slate-500" : ""}
                                />
                              </td>
                              <td className="p-2">
                                <Input size="small" type="number" value={row.qty} onChange={e => handleDetailChange(row.key, 'qty', e.target.value)} className="border-yellow-400 focus:border-yellow-500 focus:shadow-[0_0_0_2px_rgba(250,204,21,0.2)]" />
                              </td>
                              <td className="p-2">
                                <Input size="small" type="number" value={row.rate} onChange={e => handleDetailChange(row.key, 'rate', e.target.value)} />
                              </td>
                              <td className="p-2 text-right text-xs text-slate-600">
                                {calcs.baseAmt.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                              </td>
                              {isGstEnabled && (
                                <>
                                  <td className="p-2">
                                    <Input size="small" type="number" value={row.gst} onChange={e => handleDetailChange(row.key, 'gst', e.target.value)} />
                                  </td>
                                  <td className="p-2 text-right text-xs text-slate-600">
                                    {(calcs.gstAmt + calcs.addTaxAmt).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                                  </td>
                                </>
                              )}
                              <td className="p-2 text-right text-xs font-bold text-slate-800">
                                {calcs.netAmt.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                              </td>
                              <td className="p-2 text-center">
                                <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => handleRemoveItem(row.key)} />
                              </td>
                            </tr>
                          );
                        })}
                        {details.length === 0 && (
                          <tr>
                            <td colSpan={isGstEnabled ? 11 : 8} className="p-8 text-center text-slate-400 text-xs">
                              No items added. Click "Add Item" to begin.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Remarks at the Last (Bottom Section) */}
                <div className="mt-3">
                  <Form.Item name="remarks" label={<span className="text-xs font-semibold text-slate-700">Remarks (Overall)</span>} className="mb-0">
                    <TextArea autoSize={{ minRows: 3, maxRows: 8 }} placeholder="Internal notes (line by line)..." className="text-sm rounded-lg whitespace-pre-wrap font-sans" />
                  </Form.Item>
                </div>

                <div className="px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg mt-3 flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
                  <div className="text-xs font-semibold text-slate-700">
                    Total Qty: <span className="text-blue-600 text-sm ml-1">{totalQty.toLocaleString()}</span>
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-4">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-slate-500">Gross:</span>
                      <span className="text-xs font-semibold text-slate-700">{totalAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-red-600">Discount (-):</span>
                      <Form.Item name="discount" className="mb-0">
                        <InputNumber
                          size="small"
                          min={0}
                          placeholder="0.00"
                          className="w-28 text-right font-medium"
                        />
                      </Form.Item>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-emerald-700">Delivery Charges (+):</span>
                      <Form.Item name="delivery_charges" className="mb-0">
                        <InputNumber
                          size="small"
                          min={0}
                          placeholder="0.00"
                          className="w-28 text-right font-medium"
                        />
                      </Form.Item>
                    </div>
                    <div className="flex items-center gap-2 border-l border-slate-300 pl-3">
                      <span className="text-xs font-bold text-slate-800">Total Invoice Amount:</span>
                      <span className="text-base font-bold text-blue-600">{netFinalInvoiceAmount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                  </div>
                </div>

              </Form>
          </FormLayout>
        )}

        <Modal
          title={<span className="text-sm font-bold text-slate-800">Select Item</span>}
          open={isItemModalOpen}
          onCancel={() => { setIsItemModalOpen(false); setActiveRowKey(null); }}
          footer={null}
          width={650}
          destroyOnClose
          centered
        >
          <Input
            placeholder="Search item by name or code..."
            prefix={<SearchOutlined className="text-slate-400" />}
            value={itemSearchQuery}
            onChange={e => setItemSearchQuery(e.target.value)}
            className="mb-4"
            allowClear
            autoFocus
          />
          <Table
            dataSource={items.filter(item => {
              const query = itemSearchQuery.toLowerCase();
              return (
                (item.item_name2 && item.item_name2.toLowerCase().includes(query)) ||
                (item.item_name && item.item_name.toLowerCase().includes(query)) ||
                (item.item_code && item.item_code.toString().includes(query))
              );
            })}
            columns={[
              { title: 'CODE', dataIndex: 'item_code', key: 'item_code', width: 100, className: 'text-xs text-slate-600 font-semibold' },
              { title: 'ITEM NAME', key: 'item_name', className: 'text-xs font-medium text-slate-700', render: (_, record) => record.item_name2 || record.item_name },
              { title: 'UNIT', dataIndex: 'unit', key: 'unit', width: 100, className: 'text-xs text-slate-500' },
              { title: 'STOCK', dataIndex: 'stock', key: 'stock', width: 100, align: 'right', className: 'text-xs font-semibold text-slate-600', render: val => val ?? 0 }
            ]}
            rowKey="item_code"
            size="small"
            pagination={{ pageSize: 8 }}
            onRow={(record) => ({
              onClick: () => {
                handleDetailChange(activeRowKey, 'item_code', record.item_code);
                setIsItemModalOpen(false);
                setActiveRowKey(null);
              },
              className: 'cursor-pointer hover:bg-blue-50/40 transition-colors'
            })}
            locale={{ emptyText: <div className="py-8 text-slate-400">No items found</div> }}
          />
        </Modal>

      </>
  );
}
