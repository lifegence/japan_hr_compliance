// 事業場別の常時使用労働者数（JP）（EMP-33）
frappe.query_reports["JP Workplace Headcount"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "date", label: __("基準日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "workplace", label: __("事業場"), fieldtype: "Link", options: "JP Workplace" },
	],
};
