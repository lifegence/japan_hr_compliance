// 年5日の未達見込みの一覧（B-3 LVR-53）
frappe.query_reports["JP Five Day Status"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "as_of", label: __("基準日"), fieldtype: "Date", default: frappe.datetime.get_today() },
	],
};
