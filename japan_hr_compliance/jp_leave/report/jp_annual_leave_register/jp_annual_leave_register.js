// 年次有給休暇管理簿（労基則24条の7。B-3 LVR-60〜62）
frappe.query_reports["JP Annual Leave Register"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("期間（開始）"), fieldtype: "Date", default: frappe.datetime.add_months(frappe.datetime.get_today(), -36) },
		{ fieldname: "to_date", label: __("期間（終了）"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "department", label: __("部署"), fieldtype: "Link", options: "Department" },
		{ fieldname: "employee", label: __("従業員"), fieldtype: "Link", options: "Employee" },
	],
	onload(report) {
		const method = "japan_hr_compliance.jp_leave.report.jp_annual_leave_register.jp_annual_leave_register.download";
		for (const fmt of ["PDF", "Excel"]) {
			report.page.add_inner_button(__("{0} で出力", [fmt]), () => {
				const filters = JSON.stringify(report.get_values());
				window.open(`/api/method/${method}?fmt=${fmt}&filters=${encodeURIComponent(filters)}`);
			});
		}
	},
};
