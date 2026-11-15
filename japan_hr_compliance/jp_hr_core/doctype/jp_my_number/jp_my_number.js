// マイナンバー（EMP-60〜67）。番号は常に全桁マスク。表示は事務取扱担当者が理由を入力した場合のみ
frappe.ui.form.on("JP My Number", {
	setup(frm) {
		frm.set_query("employee", () => ({ filters: { status: ["!=", ""] } }));
	},
	refresh(frm) {
		const officer = frappe.user.has_role("JP My Number Officer") && frappe.session.user !== "Administrator";
		if (!frm.is_new() && officer && frm.doc.status === "有効") {
			frm.add_custom_button(__("番号を表示"), () => {
				frappe.prompt([
					{ fieldname: "reason_type", label: __("理由の区分"), fieldtype: "Select", reqd: 1,
						options: ["", "届出書の作成（社会保険）", "届出書の作成（雇用保険）", "源泉徴収票・支払調書等の作成", "扶養控除等申告書の確認", "本人確認・登録内容の確認", "その他"].join("\n") },
					{ fieldname: "reason", label: __("理由（具体的な事務）"), fieldtype: "Small Text", reqd: 1 },
				], (v) => {
					frappe.call({ method: "japan_hr_compliance.jp_hr_core.my_number.reveal", args: { name: frm.doc.name, ...v } })
						.then((r) => frappe.msgprint({ title: __("個人番号"), message: frappe.utils.escape_html(r.message), indicator: "orange" }));
				}, __("番号を表示する理由"), __("表示"));
			});
			if (frm.doc.disposal_due && frm.doc.disposal_due <= frappe.datetime.get_today()) {
				frm.add_custom_button(__("廃棄"), () => {
					frappe.confirm(__("番号の暗号文を削除します。元に戻せません。よろしいですか。"), () => {
						frappe.call({ method: "japan_hr_compliance.jp_hr_core.my_number.dispose", args: { name: frm.doc.name } })
							.then(() => frm.reload_doc());
					});
				});
			}
		}
	},
	employee(frm) {
		frm.trigger("owner_type");
	},
	owner_type(frm) {
		if (frm.doc.owner_type !== "扶養親族" || !frm.doc.employee) return;
		frappe.call({ method: "japan_hr_compliance.jp_hr_core.my_number.get_dependent_options", args: { employee: frm.doc.employee } })
			.then((r) => {
				const rows = r.message || [];
				if (!rows.length) return;
				frappe.prompt([{ fieldname: "row", label: __("扶養親族"), fieldtype: "Select", reqd: 1,
					options: rows.map((x) => ({ value: x.name, label: `${x.dependent_name}（${x.relation || ""}）` })) }],
				(v) => {
					const row = rows.find((x) => x.name === v.row);
					frm.set_value("dependent_row", row.name);
					frm.set_value("dependent_name", row.dependent_name);
				}, __("扶養親族を選ぶ"));
			});
	},
});
