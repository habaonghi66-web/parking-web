const $ = (id) => document.getElementById(id);

function setMsg(msg, ok = false) {
  const el = $("authMsg");
  el.textContent = msg || "";
  el.style.color = ok ? "#4ade80" : "#f87171";
}

async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  return await r.json();
}

function getRoleFromUrl() {
  const qs = new URLSearchParams(window.location.search);
  const role = (qs.get("role") || "staff").toLowerCase();
  return role === "admin" ? "admin" : "staff";
}

function renderRoleUI(role) {
  $("roleBadge").textContent = role === "admin" ? "Đăng nhập Admin" : "Đăng nhập Nhân viên";

  const hint = $("loginHint");
  if (hint) {
    hint.innerHTML = "";
    hint.style.display = "none";
  }

  if (role === "admin") {
    $("btnForgotAdmin").classList.remove("hidden");
  } else {
    $("btnForgotAdmin").classList.add("hidden");
  }
}

window.addEventListener("load", async () => {
  const role = getRoleFromUrl();
  renderRoleUI(role);

  $("btnLogin").onclick = async () => {
    setMsg("");

    const username = $("loginUsername").value.trim();
    const password = $("loginPassword").value.trim();

    if (!username || !password) {
      setMsg("Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu");
      return;
    }

    const data = await jpost("/api/login", { username, password, role });

    if (!data.ok) {
      setMsg(data.msg || "Đăng nhập thất bại");
      return;
    }

    setMsg("Đăng nhập thành công", true);
    window.location.href = "/";
  };

  $("btnForgotAdmin").onclick = () => {
    alert("Vui lòng liên hệ quản trị hệ thống để cấp lại mật khẩu admin.");
  };

  try {
    const r = await fetch("/api/me", { cache: "no-store" });
    const data = await r.json();
    if (data.ok && data.logged_in) {
      window.location.href = "/";
    }
  } catch (e) {
    console.error(e);
  }
});