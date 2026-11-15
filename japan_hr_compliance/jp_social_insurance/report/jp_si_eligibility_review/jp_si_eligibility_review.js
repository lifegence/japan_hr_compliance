// 短時間労働者・雇用保険の適用判定の一覧（SIR-70〜73）
frappe.query_reports["JP SI Eligibility Review"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "date", label: __("判定日"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "only_differs", label: __("登録上の資格と異なる者のみ"), fieldtype: "Check" },
	],
};
