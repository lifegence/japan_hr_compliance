// 定時決定の一括作成・随時改定の候補抽出・保険者算定（年間平均）の候補（SIR-40・44・48）
frappe.listview_settings["JP Remuneration Determination"] = {
	onload(listview) {
		const run = (method, fields, label) => frappe.prompt(fields, (v) =>
			frappe.call({ method: `japan_hr_compliance.jp_social_insurance.determination.${method}`, args: v, freeze: true })
				.then((r) => { frappe.msgprint(__("{0}：{1} 件作成しました", [label, (r.message || []).length])); listview.refresh(); }),
			label);
		const company = { fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1,
			default: frappe.defaults.get_user_default("Company") };
		const year = { fieldname: "year", label: __("年"), fieldtype: "Int", reqd: 1, default: new Date().getFullYear() };
		listview.page.add_menu_item(__("定時決定を一括作成"), () => run("create_regular_determinations", [company, year], __("定時決定")));
		listview.page.add_menu_item(__("随時改定の候補を抽出"), () => run("extract_occasional_candidates",
			[company, { fieldname: "as_of_month", label: __("給与が確定した最新の月"), fieldtype: "Date", reqd: 1 }], __("随時改定")));
		listview.page.add_menu_item(__("保険者算定（年間平均）の候補"), () => run("create_annual_average_assessments", [company, year],
			__("保険者算定")));
	},
};
