const $ = (id) => document.getElementById(id);
let CURRENT_USER = null;

function setErr(msg) {
  $("err").textContent = msg || "";
}

function openModal(title, imgUrl) {
  $("modalTitle").textContent = title || "";
  $("modalImg").src = imgUrl || "";
  $("modal").classList.remove("hidden");
}

function closeModal() {
  $("modal").classList.add("hidden");
  $("modalImg").src = "";
}

$("btnClose").onclick = closeModal;
$("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});

async function jget(url) {
  const r = await fetch(url, { cache: "no-store" });
  const data = await r.json();
  if (!r.ok || data.ok === false) throw new Error(data.msg || "Request failed");
  return data;
}

async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const data = await r.json();
  if (!r.ok || data.ok === false) throw new Error(data.msg || "Request failed");
  return data;
}

async function jput(url, body) {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const data = await r.json();
  if (!r.ok || data.ok === false) throw new Error(data.msg || "Request failed");
  return data;
}

async function jdelete(url) {
  const r = await fetch(url, { method: "DELETE" });
  const data = await r.json();
  if (!r.ok || data.ok === false) throw new Error(data.msg || "Request failed");
  return data;
}

async function ensureLoggedIn() {
  const data = await jget("/api/me");
  CURRENT_USER = data.user;
  $("userInfo").textContent = `${CURRENT_USER.full_name} (${CURRENT_USER.role})`;

  const isAdmin = CURRENT_USER.role === "admin";
  if (isAdmin) {
    $("btnManageStaff").classList.remove("hidden");
  }
}

function imgCell(imageUrl, title) {
  if (!imageUrl) return `<span class="muted">Chưa có</span>`;
  return `<button class="btn small" data-img="${imageUrl}" data-title="${title}">Xem</button>`;
}

function wireImageButtons() {
  document.querySelectorAll("button[data-img]").forEach(btn => {
    btn.onclick = () => openModal(btn.dataset.title || "Ảnh", btn.dataset.img);
  });
}

function overtimeBadge(code, text) {
  if (code === "over24") return `<span class="tagDanger">${text}</span>`;
  if (code === "over12") return `<span class="tagWarn">${text}</span>`;
  return `<span class="tagNormal">${text}</span>`;
}

async function loadStats() {
  const s = await jget("/api/stats");
  $("stats").innerHTML = `
    <div class="pill">Sức chứa: <b>${s.capacity}</b></div>
    <div class="pill">Đang trong bãi: <b>${s.active_count}</b></div>
    <div class="pill ${s.is_full ? "pillRed" : ""}">Còn trống: <b>${s.slots_left}</b></div>
  `;
}

async function loadActive() {
  const rows = await jget("/api/active");
  const tb = $("activeBody");
  const isAdmin = CURRENT_USER && CURRENT_USER.role === "admin";

  if (!rows || rows.length === 0) {
    tb.innerHTML = `<tr><td colspan="8" class="muted">Không có xe trong bãi</td></tr>`;
    return;
  }

  tb.innerHTML = rows.map(r => `
    <tr>
      <td>${r.uid || ""}</td>
      <td><b>${r.plate || ""}</b></td>
      <td>${r.vehicle_type || "Xe máy"}</td>
      <td>${r.time_in_str || ""}</td>
      <td>${overtimeBadge(r.overtime_code, r.overtime_status || "Bình thường")}</td>
      <td>${r.note || ""}</td>
      <td>${imgCell(r.image_url, `Active ${r.plate || ""}`)}</td>
      <td>
        ${isAdmin
          ? `<button class="btn small danger" data-force="${r.plate || ""}">Force OUT</button>`
          : `<span class="muted">Không có quyền</span>`
        }
      </td>
    </tr>
  `).join("");

  document.querySelectorAll("button[data-force]").forEach(btn => {
    btn.onclick = async () => {
      const plate = btn.dataset.force;
      if (!confirm(`Force OUT xe biển ${plate}?`)) return;
      try {
        setErr("");
        await jpost("/api/force_out", { plate });
        await refreshAll();
      } catch (e) {
        setErr(String(e.message || e));
      }
    };
  });

  wireImageButtons();
}

async function loadEvents() {
  const search = $("txtSearch").value.trim();
  const dir = $("selDir").value;
  const from = $("dateFrom").value;
  const to = $("dateTo").value;
  const limit = $("selLimit").value;

  const qs = new URLSearchParams({
    q: search,
    dir: dir,
    from_date: from,
    to_date: to,
    limit: limit
  });

  const rows = await jget("/api/events?" + qs.toString());
  const tb = $("eventsBody");

  if (!rows || rows.length === 0) {
    tb.innerHTML = `<tr><td colspan="8" class="muted">Không có log</td></tr>`;
    return;
  }

  tb.innerHTML = rows.map(r => `
    <tr>
      <td>${r.id}</td>
      <td><b>${r.plate || ""}</b></td>
      <td>${r.vehicle_type || "Xe máy"}</td>
      <td class="${r.direction === "IN" ? "tagIn" : "tagOut"}">${r.direction}</td>
      <td>${r.uid || ""}</td>
      <td>${r.time_str || ""}</td>
      <td>${r.note || ""}</td>
      <td>${imgCell(r.image_url, `${r.direction} ${r.plate} (${r.time_str})`)}</td>
    </tr>
  `).join("");

  wireImageButtons();
}

async function refreshAll(isManual = false) {
  try {
    setErr("");
    if (isManual) {
      await loadEvents();
      return;
    }
    await loadStats();
    await loadActive();
    await loadEvents();
  } catch (e) {
    setErr(String(e.message || e));
  }
}

$("btnRefresh").onclick = () => refreshAll(false);
$("btnSearchDate").onclick = () => refreshAll(true);

["txtSearch", "selDir", "dateFrom", "dateTo", "selLimit"].forEach(id => {
  $(id).addEventListener("change", refreshAll);
  if (id === "txtSearch") {
    $(id).addEventListener("input", () => {
      clearTimeout(window.__t);
      window.__t = setTimeout(refreshAll, 250);
    });
  }
});

let timer = null;

$("chkAuto").onchange = () => {
  if (timer) clearInterval(timer);
  if ($("chkAuto").checked) timer = setInterval(refreshAll, 2000);
};

$("btnLogout").onclick = async () => {
  try {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/login";
  } catch (e) {
    alert("Không thể đăng xuất");
  }
};

async function loadLostCards() {
  const lostList = $("lostList");
  const rows = await jget("/api/lost_cards");
  const isAdmin = CURRENT_USER && CURRENT_USER.role === "admin";

  if (!rows || rows.length === 0) {
    lostList.innerHTML = `<div class="muted">Chưa có danh sách xe mất thẻ</div>`;
    return;
  }

  lostList.innerHTML = rows.map(r => `
    <div class="lostCardItem">
      <div><b>Biển số:</b> ${r.plate || ""}</div>
      <div><b>Thời gian:</b> ${r.time_str || ""}</div>
      <div class="lostCardActions">
        ${r.vehicle_image_url ? `<button class="btn small" data-img="${r.vehicle_image_url}" data-title="Ảnh xe">Ảnh xe</button>` : ""}
        ${r.document_image_url ? `<button class="btn small" data-img="${r.document_image_url}" data-title="Giấy tờ 1">Giấy tờ 1</button>` : ""}
        ${r.document_image_url_2 ? `<button class="btn small" data-img="${r.document_image_url_2}" data-title="Giấy tờ 2">Giấy tờ 2</button>` : ""}
        ${isAdmin
          ? `<button class="btn small danger" data-del-lost="${r.id}">Xóa</button>`
          : `<span class="muted">Chỉ admin được xóa</span>`
        }
      </div>
      <hr>
    </div>
  `).join("");

  wireImageButtons();

  document.querySelectorAll("[data-del-lost]").forEach(btn => {
    btn.onclick = async () => {
      if (!confirm("Xóa mục mất thẻ này?")) return;
      try {
        await jdelete(`/api/lost_card/${btn.dataset.delLost}`);
        await loadLostCards();
      } catch (e) {
        alert(e.message || e);
      }
    };
  });
}

async function loadStaffAccounts() {
  const data = await jget("/api/staff_accounts");
  const tb = $("staffBody");

  if (!data.items || data.items.length === 0) {
    tb.innerHTML = `<tr><td colspan="6" class="muted">Chưa có tài khoản nhân viên</td></tr>`;
    return;
  }

  tb.innerHTML = data.items.map(r => `
    <tr>
      <td>${r.id}</td>
      <td><b>${r.username}</b></td>
      <td>${r.full_name || ""}</td>
      <td>${r.is_active == 1 ? '<span class="status-active">Đang hoạt động</span>' : '<span class="status-locked">Đang khóa</span>'}</td>
      <td>${r.created_at_str || ""}</td>
      <td class="staffActionWrap">
        <button class="btn small" data-edit-staff="${r.id}" data-name="${r.full_name || ""}" data-active="${r.is_active}">Sửa</button>
        <button class="btn small" data-pass-staff="${r.id}">Đổi mật khẩu</button>
        <button class="btn small danger" data-remove-staff="${r.id}">Xóa</button>
      </td>
    </tr>
  `).join("");

  document.querySelectorAll("[data-edit-staff]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.editStaff;
      const currentName = btn.dataset.name || "";
      const currentActive = btn.dataset.active === "1";

      const full_name = prompt("Nhập họ tên nhân viên:", currentName);
      if (full_name === null) return;

      const action = prompt(
        `Nhập trạng thái:\n1 = mở tài khoản\n0 = khóa tài khoản`,
        currentActive ? "1" : "0"
      );
      if (action === null) return;

      const is_active = action === "0" ? 0 : 1;

      try {
        await jput(`/api/staff_accounts/${id}`, { full_name, is_active });
        await loadStaffAccounts();
        alert("Đã cập nhật tài khoản nhân viên");
      } catch (e) {
        alert(e.message || e);
      }
    };
  });

  document.querySelectorAll("[data-pass-staff]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.passStaff;
      const password = prompt("Nhập mật khẩu mới:");
      if (password === null || !password.trim()) return;
      try {
        await jput(`/api/staff_accounts/${id}/password`, { password });
        alert("Đã đổi mật khẩu");
      } catch (e) {
        alert(e.message || e);
      }
    };
  });

  document.querySelectorAll("[data-remove-staff]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.removeStaff;
      if (!confirm("Xóa tài khoản nhân viên này?")) return;
      try {
        await jdelete(`/api/staff_accounts/${id}`);
        await loadStaffAccounts();
      } catch (e) {
        alert(e.message || e);
      }
    };
  });
}

window.addEventListener("load", async () => {
  try {
    await ensureLoggedIn();
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch (e) {
    console.error(e);
    return;
  }

  const lostModal = $("lostModal");
  const btnLostFloating = $("btnLostFloating");
  const btnCloseLost = $("btnCloseLost");
  const btnFindLost = $("btnFindLost");
  const btnSendLost = $("btnSendLost");
  const lostPlate = $("lostPlate");
  const lostVehicleImg = $("lostVehicleImg");
  const lostDocInput = $("lostDocInput");
  const lostDocPreview = $("lostDocPreview");

  const isAdmin = CURRENT_USER && CURRENT_USER.role === "admin";
  if (!isAdmin) {
    btnLostFloating.textContent = "Danh sách xe mất thẻ";
  }

  btnLostFloating.onclick = async () => {
    lostModal.classList.remove("hidden");
    await loadLostCards();
  };

  btnCloseLost.onclick = () => {
    lostModal.classList.add("hidden");
    if (lostVehicleImg) lostVehicleImg.src = "";
    if (lostDocPreview) lostDocPreview.src = "";
    if (lostPlate) lostPlate.value = "";
    if (lostDocInput) lostDocInput.value = "";
  };

  if (lostDocInput) {
    lostDocInput.onchange = () => {
      const file = lostDocInput.files[0];
      if (!file) return;
      if (lostDocPreview) lostDocPreview.src = URL.createObjectURL(file);
    };
  }

  btnFindLost.onclick = async () => {
    const plate = (lostPlate?.value || "").trim();
    if (!plate) {
      alert("Vui lòng nhập biển số");
      return;
    }
    try {
      const data = await jget(`/api/find_vehicle_image?plate=${encodeURIComponent(plate)}`);
      if (data.ok && data.image_url) {
        lostVehicleImg.src = data.image_url;
      } else {
        alert("Không tìm thấy ảnh xe");
      }
    } catch (e) {
      alert(e.message || e);
    }
  };

  btnSendLost.onclick = async () => {
    if (!isAdmin) {
      alert("Chỉ admin mới được gửi xác minh mất thẻ");
      return;
    }

    const plate = (lostPlate?.value || "").trim();
    if (!plate) {
      alert("Vui lòng nhập biển số");
      return;
    }

    const files = lostDocInput.files || [];
    const fd = new FormData();
    fd.append("plate", plate);
    fd.append("vehicle_image_url", lostVehicleImg.src || "");
    for (let i = 0; i < files.length; i++) {
      fd.append("documents", files[i]);
    }

    try {
      const r = await fetch("/api/lost_card", { method: "POST", body: fd });
      const data = await r.json();
      if (!data.ok) throw new Error(data.msg || "Gửi thất bại");
      alert("Đã gửi xác minh mất thẻ");
      await loadLostCards();
    } catch (e) {
      alert(e.message || e);
    }
  };

  $("btnReloadLost").onclick = async () => {
    try {
      await loadLostCards();
    } catch (e) {
      alert(e.message || e);
    }
  };

  const staffModal = $("staffModal");
  const btnManageStaff = $("btnManageStaff");
  const btnCloseStaff = $("btnCloseStaff");

  if (btnManageStaff) {
    btnManageStaff.onclick = async () => {
      staffModal.classList.remove("hidden");
      await loadStaffAccounts();
    };
  }

  if (btnCloseStaff) {
    btnCloseStaff.onclick = () => staffModal.classList.add("hidden");
  }

  $("btnCreateStaff").onclick = async () => {
    const username = $("staffUsername").value.trim();
    const full_name = $("staffFullName").value.trim();
    const password = $("staffPassword").value.trim();

    if (!username || !full_name || !password) {
      alert("Vui lòng nhập đủ tên đăng nhập, họ tên và mật khẩu");
      return;
    }

    try {
      await jpost("/api/staff_accounts", { username, full_name, password });
      $("staffUsername").value = "";
      $("staffFullName").value = "";
      $("staffPassword").value = "";
      await loadStaffAccounts();
      alert("Đã thêm nhân viên mới");
    } catch (e) {
      alert(e.message || e);
    }
  };
});