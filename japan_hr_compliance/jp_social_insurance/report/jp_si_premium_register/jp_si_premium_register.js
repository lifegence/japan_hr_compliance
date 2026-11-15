// 社会保険料・雇用保険料の個人別一覧（SIR-27・63〜65、2.2 の検出）
frappe.query_reports["JP SI Premium Register"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("支給日（開始）"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("支給日（終了）"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_end() },
		{ fieldname: "employee", label: __("従業員"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "only_exceeds", label: __("本人負担が事業主負担を上回る行のみ"), fieldtype: "Check" },
	],
};
