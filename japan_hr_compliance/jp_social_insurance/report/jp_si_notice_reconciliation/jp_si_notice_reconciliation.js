// 保険料総額の試算と納入告知額の差異（SIR-27）
frappe.query_reports["JP SI Notice Reconciliation"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "month", label: __("保険料の対象月"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "notice_health", label: __("納入告知額：健康保険（介護含む）"), fieldtype: "Currency" },
		{ fieldname: "notice_support", label: __("納入告知額：子ども・子育て支援金"), fieldtype: "Currency" },
		{ fieldname: "notice_pension", label: __("納入告知額：厚生年金"), fieldtype: "Currency" },
		{ fieldname: "notice_child", label: __("納入告知額：子ども・子育て拠出金"), fieldtype: "Currency" },
	],
};
