// 労働者名簿（JP）。労基法107条・労基則53条の記載事項①〜⑨（従業員マスタ要件 B-1 v1.0 EMP-45・46）
frappe.query_reports["JP Worker Roster"] = {
	filters: [
		{ fieldname: "company", label: __("会社"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "workplace", label: __("事業場"), fieldtype: "Link", options: "JP Workplace",
			get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }) },
		{ fieldname: "employee", label: __("従業員"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "status", label: __("在籍区分"), fieldtype: "Select", options: "すべて\n在籍者\n退職者", default: "すべて" },
		{ fieldname: "from_date", label: __("期間（開始）"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("期間（終了）"), fieldtype: "Date" },
	],
	onload(report) {
		// 臨検時に指定した従業員・期間の名簿を即時に PDF で出す（EMP-91）
		report.page.add_inner_button(__("PDF を出力"), () => {
			const filters = JSON.stringify(report.get_values());
			window.open(`/api/method/japan_hr_compliance.jp_hr_core.report.jp_worker_roster.jp_worker_roster.download_roster_pdf?filters=${encodeURIComponent(filters)}`);
		});
	},
};
