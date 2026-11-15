// 健康保険料率の版（SIR-01・02・06）。CSV の取込みと保険料額表との突合（5.3）
frappe.ui.form.on("JP Health Insurance Rate", {
	refresh(frm) {
		if (frm.is_new()) return;
		const upload = (method, title, help) => {
			new frappe.ui.FileUploader({
				restrictions: { allowed_file_types: [".csv"] },
				dialog_title: title,
				on_success: (file) => frm.call(method, { file_url: file.file_url }).then((r) => {
					frm.reload_doc();
					frappe.show_alert({ message: help(r.message), indicator: "green" });
				}),
			});
		};
		frm.add_custom_button(__("料率を CSV で取込み"), () => upload("import_rates_csv", __("prefecture,health_rate,care_rate,support_rate"),
			(n) => __("{0} 件の都道府県を取り込みました", [n])), __("CSV"));
		frm.add_custom_button(__("保険料額表と突合"), () => upload("reconcile_premium_table",
			__("prefecture,grade,health_half,health_care_half,support_half"), (n) => __("不一致 {0} 件", [n])), __("CSV"));
		frm.add_custom_button(__("この版を使った給与明細"), () => frm.call("get_affected_salary_slips").then((r) => {
			const rows = (r.message || []).map((s) => `<li>${s.name}（${s.employee_name}・${s.posting_date}）</li>`).join("");
			frappe.msgprint({ title: __("影響する給与明細"), message: rows ? `<ul>${rows}</ul>` : __("ありません") });
		}));
	},
});
