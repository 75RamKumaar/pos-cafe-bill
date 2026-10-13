/* Billing Page Script - Cafe POS */
/* High-contrast, one-hand outdoor bright-light optimized script */

const cart = new Map();
const products = {};
const shortcutProducts = {};
let shortcutBuffer = "";
let shortcutTimer = null;
let selectedCartId = null;
let lastBillUrl = null;
let lastBillId = null;
try {
    lastBillUrl = sessionStorage.getItem("last_bill_url");
    lastBillId = sessionStorage.getItem("last_bill_id");
} catch (e) {}
let toastTimer = null;
let audioCtx = null;
let feedbackEnabled = false;
let currentOrderType = "Dine-in";

function setOrderType(type) {
    currentOrderType = type;
    const btnDine = document.getElementById("orderTypeDineIn");
    const btnTake = document.getElementById("orderTypeTakeaway");
    if (btnDine && btnTake) {
        if (type.toLowerCase() === "takeaway") {
            btnTake.classList.add("active");
            btnDine.classList.remove("active");
        } else {
            btnDine.classList.add("active");
            btnTake.classList.remove("active");
        }
    }
}

// Audio & Haptic Feedback (off by default)
function initFeedback() {
    try {
        feedbackEnabled = localStorage.getItem("pos_feedback_enabled") === "true";
    } catch (e) {
        feedbackEnabled = false;
    }
    updateFeedbackUI();
}

function updateFeedbackUI() {
    const btn = document.getElementById("feedbackToggleBtn");
    if (!btn) return;
    const label = document.getElementById("feedbackLabel");
    const icon = document.getElementById("feedbackIcon");
    if (feedbackEnabled) {
        if (label) label.textContent = "Feedback: ON";
        if (icon) icon.textContent = "🔔";
        btn.classList.add("btn-primary");
        btn.classList.remove("btn-outline");
    } else {
        if (label) label.textContent = "Feedback: OFF";
        if (icon) icon.textContent = "🔕";
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-outline");
    }
}

function toggleFeedback(force) {
    if (typeof force === "boolean") {
        feedbackEnabled = force;
    } else {
        feedbackEnabled = !feedbackEnabled;
    }
    try {
        localStorage.setItem("pos_feedback_enabled", feedbackEnabled ? "true" : "false");
    } catch (e) {}
    updateFeedbackUI();
    if (feedbackEnabled) {
        triggerAddFeedback();
        showToast("Audio/Haptic feedback enabled");
    } else {
        showToast("Feedback disabled");
    }
}

function triggerAddFeedback() {
    if (!feedbackEnabled) return;
    // Haptic vibration
    try {
        if (navigator.vibrate) {
            navigator.vibrate(35);
        }
    } catch (e) {}
    // Sound beep via Web Audio API
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext) {
            if (!audioCtx) audioCtx = new AudioContext();
            if (audioCtx.state === "suspended") audioCtx.resume();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = "sine";
            osc.frequency.setValueAtTime(880, audioCtx.currentTime);
            gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.05);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.05);
        }
    } catch (e) {}
}

// Mobile Bottom Sheet Toggle
function toggleCartSheet(forceOpen) {
    const panel = document.getElementById("cartPanel");
    const backdrop = document.getElementById("cartSheetBackdrop");
    if (!panel) return;
    const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : !panel.classList.contains("cart-sheet-open");
    if (shouldOpen) {
        panel.classList.add("cart-sheet-open");
        if (backdrop) backdrop.classList.add("active");
    } else {
        panel.classList.remove("cart-sheet-open");
        if (backdrop) backdrop.classList.remove("active");
    }
}

function getBillCreateUrl() {
    return document.querySelector(".billing-page")?.dataset.billCreateUrl || "/pos/bill/create/";
}

function getCustomerSearchUrl() {
    return document.querySelector(".billing-page")?.dataset.customerSearchUrl || "/pos/customer/search/";
}

function getHeldListUrl() {
    return document.querySelector(".billing-page")?.dataset.heldListUrl || "/pos/billing/held/";
}

function getHeldHoldUrl() {
    return document.querySelector(".billing-page")?.dataset.heldHoldUrl || "/pos/billing/hold/";
}

let currentModifierProduct = null;
let selectedModifierIds = new Set();

function findCartItem(keyOrId) {
    const strKey = String(keyOrId);
    if (cart.has(strKey)) return cart.get(strKey);
    for (const item of cart.values()) {
        if (String(item.id) === strKey || item.key === strKey) return item;
    }
    return null;
}

function addItem(id) {
    const product = products[id];
    if (!product) return;
    if (product.modifiers && product.modifiers.length > 0) {
        openModifierChooser(id);
    } else {
        addDirectItemToCart(product, [], "");
    }
}

function openModifierChooser(id) {
    const product = products[id];
    if (!product) return;
    currentModifierProduct = product;
    selectedModifierIds.clear();

    const dialog = document.getElementById("modifierChooserDialog");
    const title = document.getElementById("modifierChooserTitle");
    const basePriceEl = document.getElementById("modifierChooserBasePrice");
    const noteInput = document.getElementById("modifierItemNote");
    const container = document.getElementById("modifierChipsContainer");

    if (title) title.textContent = product.name;
    if (basePriceEl) basePriceEl.textContent = `Base: ₹${product.price.toFixed(2)}`;
    if (noteInput) noteInput.value = "";

    if (container) {
        container.innerHTML = "";
        product.modifiers.forEach(mod => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "modifier-chip";
            const deltaNum = parseFloat(mod.price_delta) || 0;
            const sign = deltaNum >= 0 ? "+" : "";
            btn.textContent = `${mod.name} (${sign}₹${deltaNum.toFixed(2)})`;
            btn.dataset.id = mod.id;
            btn.onclick = () => toggleModifierChip(btn, mod.id);
            container.appendChild(btn);
        });
    }

    updateModifierChooserTotal();
    if (dialog && typeof dialog.showModal === "function") {
        dialog.showModal();
    }
}

function toggleModifierChip(btn, modId) {
    const idNum = Number(modId);
    if (selectedModifierIds.has(idNum)) {
        selectedModifierIds.delete(idNum);
        btn.classList.remove("selected");
    } else {
        selectedModifierIds.add(idNum);
        btn.classList.add("selected");
    }
    updateModifierChooserTotal();
}

function updateModifierChooserTotal() {
    if (!currentModifierProduct) return;
    let total = currentModifierProduct.price;
    for (const modId of selectedModifierIds) {
        const mod = currentModifierProduct.modifiers.find(m => m.id === modId);
        if (mod) {
            total += parseFloat(mod.price_delta) || 0;
        }
    }
    const liveEl = document.getElementById("modifierChooserLiveTotal");
    if (liveEl) liveEl.textContent = `₹${total.toFixed(2)}`;
}

function closeModifierChooser() {
    const dialog = document.getElementById("modifierChooserDialog");
    if (dialog && typeof dialog.close === "function") {
        dialog.close();
    }
    currentModifierProduct = null;
    selectedModifierIds.clear();
}

function confirmModifierAdd() {
    if (!currentModifierProduct) return;
    const noteInput = document.getElementById("modifierItemNote");
    const itemNote = noteInput ? noteInput.value.trim() : "";
    const chosenMods = currentModifierProduct.modifiers.filter(m => selectedModifierIds.has(m.id));
    addDirectItemToCart(currentModifierProduct, chosenMods, itemNote);
    closeModifierChooser();
}

function addDirectItemToCart(product, chosenMods = [], itemNote = "") {
    const sortedMods = [...chosenMods].sort((a, b) => a.id - b.id);
    const modIds = sortedMods.map(m => m.id);
    const modNames = sortedMods.map(m => m.name);
    const modDeltaSum = sortedMods.reduce((sum, m) => sum + (parseFloat(m.price_delta) || 0), 0);
    const effectivePrice = Math.round((product.price + modDeltaSum) * 100) / 100;

    const key = `${product.id}_${modIds.join('-')}${itemNote ? '_' + itemNote : ''}`;

    const existing = cart.get(key);
    if (existing) {
        if (product.tracked && (existing.quantity + 1) > product.stock) {
            showShortcutFeedback(`Cannot add more. In stock: ${product.stock}`, true);
            return;
        }
        existing.quantity = Math.round((existing.quantity + 1) * 100) / 100;
    } else {
        if (product.tracked && product.stock < 1) {
            showShortcutFeedback(`Out of stock. In stock: ${product.stock}`, true);
            return;
        }
        cart.set(key, {
            key: key,
            id: product.id,
            name: product.name,
            basePrice: product.price,
            price: effectivePrice,
            effectivePrice: effectivePrice,
            stock: product.stock,
            tracked: product.tracked,
            quantity: 1,
            modifiers: sortedMods,
            modifier_ids: modIds,
            modifier_names: modNames,
            note: itemNote || ""
        });
    }
    selectedCartId = key;
    triggerAddFeedback();
    renderCart();
}

function changeQuantity(keyOrId, delta) {
    const item = findCartItem(keyOrId);
    if (!item) return;
    if (delta > 0 && item.tracked && item.quantity >= item.stock) {
        showShortcutFeedback(`Cannot add more. In stock: ${item.stock}`, true);
        return;
    }
    const nextQty = Math.round((item.quantity + delta) * 100) / 100;
    if (nextQty <= 0) {
        cart.delete(item.key);
        if (selectedCartId === item.key) selectedCartId = null;
    } else {
        if (delta > 0 && item.tracked && nextQty > item.stock) {
            showShortcutFeedback(`Cannot add more. In stock: ${item.stock}`, true);
            return;
        }
        item.quantity = nextQty;
        selectedCartId = item.key;
    }
    renderCart();
}

function updateQuantity(keyOrId, val) {
    const item = findCartItem(keyOrId);
    if (!item) return;
    let qty = parseFloat(val);
    if (isNaN(qty) || qty <= 0) {
        cart.delete(item.key);
        selectedCartId = null;
        renderCart();
        return;
    }
    qty = Math.round(qty * 100) / 100;
    if (qty < 0.25) qty = 0.25;
    if (item.tracked && qty > item.stock) {
        showShortcutFeedback(`Cannot exceed stock. In stock: ${item.stock}`, true);
        qty = item.stock;
    }
    item.quantity = qty;
    selectedCartId = item.key;
    renderCart();
}

function setCartItemQuantity(keyOrId, targetQty) {
    const item = findCartItem(keyOrId);
    if (!item) return;
    const qty = Number(targetQty);
    if (item.tracked && qty > item.stock) {
        showShortcutFeedback(`Cannot exceed stock. In stock: ${item.stock}`, true);
        return;
    }
    item.quantity = qty;
    selectedCartId = item.key;
    triggerAddFeedback();
    renderCart();
}

function removeItem(keyOrId) {
    const item = findCartItem(keyOrId);
    if (item) {
        cart.delete(item.key);
        if (selectedCartId === item.key) selectedCartId = null;
        renderCart();
    }
}

function clearCart() {
    cart.clear();
    const discountEl = document.getElementById("discount");
    if (discountEl) discountEl.value = "0";
    const noteEl = document.getElementById("billNote");
    if (noteEl) noteEl.value = "";
    const t = document.getElementById("tenderedAmount");
    if (t) t.value = "";
    selectedCartId = null;
    const s = document.getElementById("splitPaymentToggle");
    if (s && s.checked) {
        s.checked = false;
        toggleSplitPayment(false);
    }
    setOrderType("Dine-in");
    renderCart();
}

function renderCart() {
    const cartEl = document.getElementById("cart");
    let subtotal = 0;
    let count = 0;
    if (!cart.size) {
        if (cartEl) cartEl.innerHTML = '<div class="cart-empty">Your bill is empty.<br>Choose items from the menu.</div>';
    } else {
        if (cartEl) {
            cartEl.innerHTML = [...cart.values()].map(item => {
                const total = item.price * item.quantity;
                subtotal += total;
                count += item.quantity;
                const modText = item.modifier_names && item.modifier_names.length
                    ? `+ ${item.modifier_names.join(', ')}`
                    : '';
                return `<div class="cart-item">
                    <div style="flex:1;min-width:0">
                        <div class="cart-item-name">${escapeHtml(item.name)}</div>
                        ${modText ? `<div class="cart-item-modifiers">${escapeHtml(modText)}</div>` : ''}
                        ${item.note ? `<div class="cart-item-note">Note: ${escapeHtml(item.note)}</div>` : ''}
                        <span class="cart-item-price">₹${item.price.toFixed(2)} each</span>
                        <div class="quantity-stepper">
                            <button type="button" aria-label="Decrease ${escapeHtml(item.name)} quantity" onclick="changeQuantity('${escapeHtml(item.key)}', -1)">-</button>
                            <input type="number" min="0.25" step="0.01" value="${item.quantity}" class="cart-qty-input" aria-label="Quantity for ${escapeHtml(item.name)}" onchange="updateQuantity('${escapeHtml(item.key)}', this.value)" onfocus="this.select()">
                            <button type="button" aria-label="Increase ${escapeHtml(item.name)} quantity" onclick="changeQuantity('${escapeHtml(item.key)}', 1)">+</button>
                            <button type="button" class="cart-remove" aria-label="Remove ${escapeHtml(item.name)} from cart" onclick="removeItem('${escapeHtml(item.key)}')">&#215;</button>
                        </div>
                        <div class="quick-qty-chips">
                            <button type="button" class="qty-chip" onclick="setCartItemQuantity('${escapeHtml(item.key)}', 2)">x2</button>
                            <button type="button" class="qty-chip" onclick="setCartItemQuantity('${escapeHtml(item.key)}', 5)">x5</button>
                            <button type="button" class="qty-chip" onclick="setCartItemQuantity('${escapeHtml(item.key)}', 10)">x10</button>
                            <button type="button" class="qty-chip" onclick="setCartItemQuantity('${escapeHtml(item.key)}', 20)">x20</button>
                        </div>
                    </div>
                    <div class="cart-item-total">₹${total.toFixed(2)}</div>
                </div>`;
            }).join("");
        }
    }
    if (!cart.size) subtotal = 0;
    count = Math.round(count * 100) / 100;
    const discountEl = document.getElementById("discount");
    const discount = Math.max(0, Number(discountEl ? discountEl.value : 0) || 0);
    const total = Math.max(0, subtotal - discount);

    const subtotalEl = document.getElementById("subtotal");
    if (subtotalEl) subtotalEl.textContent = subtotal.toFixed(2);
    const grandTotalEl = document.getElementById("grandTotal");
    if (grandTotalEl) grandTotalEl.textContent = total.toFixed(2);
    const cartCountEl = document.getElementById("cartCount");
    if (cartCountEl) cartCountEl.textContent = `${count} item${count === 1 ? "" : "s"}`;
    const mobileDockCountEl = document.getElementById("mobileDockCount");
    if (mobileDockCountEl) mobileDockCountEl.textContent = `${count} item${count === 1 ? "" : "s"}`;
    const mobileDockTotalEl = document.getElementById("mobileDockTotal");
    if (mobileDockTotalEl) mobileDockTotalEl.textContent = total.toFixed(2);

    updateChangeDue();
    updateSplitAmounts();
    updateGenerateButtonState();
}

function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        "'": '&#39;',
        '"': '&quot;'
    }[character]));
}

function getCookie(name) {
    const row = document.cookie.split("; ").find((part) => part.startsWith(name + "="));
    return row ? decodeURIComponent(row.split("=")[1]) : "";
}

function showShortcutFeedback(message, isError = false) {
    const feedback = document.getElementById("shortcutFeedback");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.style.color = isError ? "var(--billing-danger, #c63d3d)" : "";
}

function addShortcutCode(code) {
    const product = shortcutProducts[code.trim().toUpperCase()];
    if (!product) {
        showShortcutFeedback(`No product for ${code.trim() || "that code"}`, true);
        return false;
    }
    addItem(product.id);
    showShortcutFeedback(`Added ${product.name}`);
    return true;
}

function showToast(message) {
    const toast = document.getElementById("billingToast");
    if (!toast) return;
    toast.textContent = message;
    toast.style.display = "flex";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        toast.style.display = "none";
        toastTimer = null;
    }, 4000);
}

function printReceipt(url) {
    let iframe = document.getElementById("receiptPrintFrame");
    if (!iframe) {
        iframe = document.createElement("iframe");
        iframe.id = "receiptPrintFrame";
        iframe.name = "receiptPrintFrame";
        iframe.style.position = "fixed";
        iframe.style.right = "0";
        iframe.style.bottom = "0";
        iframe.style.width = "0";
        iframe.style.height = "0";
        iframe.style.border = "0";
        iframe.style.visibility = "hidden";
        iframe.setAttribute("aria-hidden", "true");
        document.body.appendChild(iframe);
    }
    iframe.src = url;
}

function updateReprintButton() {
    const btn = document.getElementById("reprintLastBtn");
    if (!btn) return;
    btn.disabled = !(lastBillUrl || lastBillId);
}

async function reprintLastBill() {
    if (!lastBillUrl && !lastBillId) return;
    showToast("Reprinting last bill...");
    if (lastBillId) {
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 2500);
            const resp = await fetch(`/billing/${lastBillId}/print/?is_duplicate=1`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCookie("csrftoken")
                },
                body: JSON.stringify({ is_duplicate: true }),
                signal: controller.signal
            });
            clearTimeout(timeout);
            const result = await resp.json();
            if (resp.ok && result.success) {
                showToast("Duplicate receipt sent to thermal printer.");
                return;
            } else {
                throw new Error(result.error || "Thermal print failed");
            }
        } catch (err) {
            console.warn("Thermal reprint failed, opening browser print:", err);
            showToast("Thermal reprint unavailable. Opening browser print dialog...");
        }
    }
    if (lastBillUrl) {
        printReceipt(lastBillUrl);
    }
}

function getCurrentGrandTotal() {
    let subtotal = 0;
    for (const item of cart.values()) {
        subtotal += item.price * item.quantity;
    }
    const discountEl = document.getElementById("discount");
    const discount = Math.max(0, Number(discountEl ? discountEl.value : 0) || 0);
    return Math.max(0, subtotal - discount);
}

function updateChangeDue() {
    const total = getCurrentGrandTotal();
    const input = document.getElementById("tenderedAmount");
    const display = document.getElementById("changeDueDisplay");
    if (!display) return;
    const val = input ? input.value.trim() : "";
    if (!val) {
        display.textContent = "₹0.00";
        display.style.color = "var(--billing-ink)";
        return;
    }
    const num = parseFloat(val);
    if (isNaN(num)) {
        display.textContent = "₹0.00";
        display.style.color = "var(--billing-ink)";
        return;
    }
    const change = num - total;
    if (change >= 0) {
        display.textContent = "₹" + change.toFixed(2);
        display.style.color = "var(--billing-success, #198754)";
    } else {
        display.textContent = "₹0.00 (Short ₹" + Math.abs(change).toFixed(2) + ")";
        display.style.color = "var(--billing-danger, #c63d3d)";
    }
}

function setQuickTendered(val) {
    const input = document.getElementById("tenderedAmount");
    if (!input) return;
    if (val === "exact") {
        input.value = getCurrentGrandTotal().toFixed(2);
    } else {
        input.value = Number(val).toFixed(2);
    }
    updateChangeDue();
}

function updatePaymentMethodUI() {
    const isSplit = document.getElementById("splitPaymentToggle") && document.getElementById("splitPaymentToggle").checked;
    if (isSplit) return;
    const selected = document.querySelector('input[name="payment_method"]:checked');
    const section = document.getElementById("cashTenderedSection");
    if (!section) return;
    if (selected && selected.value === "CASH") {
        section.style.display = "block";
        updateChangeDue();
    } else {
        section.style.display = "none";
    }
}

function toggleSplitPayment(isSplit) {
    const splitSection = document.getElementById("splitPaymentSection");
    const paymentGrid = document.getElementById("paymentGrid");
    const cashSection = document.getElementById("cashTenderedSection");
    if (isSplit) {
        if (paymentGrid) paymentGrid.style.display = "none";
        if (cashSection) cashSection.style.display = "none";
        if (splitSection) splitSection.style.display = "block";
        const container = document.getElementById("splitRowsContainer");
        if (container && !container.children.length) {
            const total = getCurrentGrandTotal();
            addSplitRow("CASH", total > 0 ? total.toFixed(2) : "");
            addSplitRow("UPI", "");
        }
        updateSplitAmounts();
    } else {
        if (paymentGrid) paymentGrid.style.display = "grid";
        if (splitSection) splitSection.style.display = "none";
        updatePaymentMethodUI();
        updateGenerateButtonState();
    }
}

function addSplitRow(defaultMethod = "CASH", defaultAmount = "") {
    const container = document.getElementById("splitRowsContainer");
    if (!container) return;
    const row = document.createElement("div");
    row.className = "split-row";
    row.innerHTML = `<select class="split-method" onchange="updateSplitAmounts()">
        <option value="CASH"${defaultMethod === "CASH" ? " selected" : ""}>Cash</option>
        <option value="UPI"${defaultMethod === "UPI" ? " selected" : ""}>UPI</option>
        <option value="CARD"${defaultMethod === "CARD" ? " selected" : ""}>Card</option>
        <option value="KHATA"${defaultMethod === "KHATA" ? " selected" : ""}>Khata (credit)</option>
    </select>
    <input type="number" step="0.01" min="0.01" class="split-amount" value="${defaultAmount}" placeholder="0.00" oninput="updateSplitAmounts()">
    <button type="button" class="btn btn-soft" onclick="removeSplitRow(this)">&#215;</button>`;
    container.appendChild(row);
    updateSplitAmounts();
}

function removeSplitRow(btn) {
    const row = btn.closest(".split-row");
    if (row) {
        row.remove();
        updateSplitAmounts();
    }
}

function updateSplitAmounts() {
    const isSplit = document.getElementById("splitPaymentToggle") && document.getElementById("splitPaymentToggle").checked;
    if (!isSplit) return;
    const total = getCurrentGrandTotal();
    const rows = document.querySelectorAll("#splitRowsContainer .split-row");
    let sum = 0;
    rows.forEach(r => {
        const val = parseFloat(r.querySelector(".split-amount").value) || 0;
        sum += val;
    });
    sum = Math.round(sum * 100) / 100;
    const remaining = Math.round((total - sum) * 100) / 100;
    const remainingEl = document.getElementById("splitRemaining");
    if (remainingEl) {
        if (Math.abs(remaining) < 0.005) {
            remainingEl.textContent = "₹0.00";
            remainingEl.style.color = "var(--billing-success, #198754)";
        } else if (remaining > 0) {
            remainingEl.textContent = `₹${remaining.toFixed(2)}`;
            remainingEl.style.color = "var(--billing-danger, #c63d3d)";
        } else {
            remainingEl.textContent = `-₹${Math.abs(remaining).toFixed(2)}`;
            remainingEl.style.color = "var(--billing-danger, #c63d3d)";
        }
    }
    updateGenerateButtonState();
}

function updateGenerateButtonState() {
    const btn = document.getElementById("generateBillButton");
    if (!btn) return;
    if (!navigator.onLine) {
        btn.disabled = true;
        return;
    }
    const isSplit = document.getElementById("splitPaymentToggle") && document.getElementById("splitPaymentToggle").checked;
    if (isSplit) {
        const total = getCurrentGrandTotal();
        const rows = document.querySelectorAll("#splitRowsContainer .split-row");
        let sum = 0;
        rows.forEach(r => {
            const val = parseFloat(r.querySelector(".split-amount").value) || 0;
            sum += val;
        });
        sum = Math.round(sum * 100) / 100;
        const remainder = Math.round((total - sum) * 100) / 100;
        btn.disabled = (!cart.size || Math.abs(remainder) >= 0.005);
    } else {
        btn.disabled = (!cart.size);
    }
}

function updateBillingOnlineStatus() {
    const warning = document.getElementById("billingOfflineWarning");
    const isOffline = !navigator.onLine;
    if (warning) {
        warning.style.display = isOffline ? "block" : "none";
    }
    updateGenerateButtonState();
}

async function saveBill() {
    const error = document.getElementById("billingError");
    if (error) error.style.display = "none";
    if (!navigator.onLine) {
        if (error) {
            error.textContent = "Offline: Billing is disabled while offline because an active network connection is required.";
            error.style.display = "block";
        }
        return;
    }
    if (!cart.size) {
        if (error) {
            error.textContent = "Add at least one item before saving.";
            error.style.display = "block";
        }
        return;
    }
    const customerId = document.getElementById("customer")?.value || null;
    const isSplit = document.getElementById("splitPaymentToggle") && document.getElementById("splitPaymentToggle").checked;
    let payloadPayments = null;
    let paymentMethod = "";
    if (isSplit) {
        const rows = document.querySelectorAll("#splitRowsContainer .split-row");
        const payments = [];
        let totalSplit = 0;
        rows.forEach(r => {
            const m = r.querySelector(".split-method").value;
            const amtStr = r.querySelector(".split-amount").value.trim();
            const amt = parseFloat(amtStr) || 0;
            if (amt > 0) {
                payments.push({ method: m, amount: amtStr });
                totalSplit += amt;
            }
        });
        if (!payments.length) {
            if (error) {
                error.textContent = "Please add at least one split payment method.";
                error.style.display = "block";
            }
            return;
        }
        const grandTotal = getCurrentGrandTotal();
        const diff = Math.round((grandTotal - totalSplit) * 100) / 100;
        if (Math.abs(diff) >= 0.005) {
            if (error) {
                error.textContent = `Payment total must equal bill total (remaining: ₹${diff.toFixed(2)}).`;
                error.style.display = "block";
            }
            return;
        }
        if (payments.some(p => p.method === "KHATA") && !customerId) {
            if (error) {
                error.textContent = "A customer is required for Khata credit checkout.";
                error.style.display = "block";
            }
            return;
        }
        payloadPayments = payments;
        paymentMethod = "SPLIT";
    } else {
        const paymentRadio = document.querySelector('input[name="payment_method"]:checked');
        paymentMethod = paymentRadio ? paymentRadio.value : "";
        if (paymentMethod === "KHATA" && !customerId) {
            if (error) {
                error.textContent = "A customer is required for Khata credit checkout.";
                error.style.display = "block";
            }
            return;
        }
    }
    const generateButton = document.getElementById("generateBillButton");
    if (generateButton) {
        generateButton.disabled = true;
        generateButton.classList.add("loading");
        generateButton.textContent = "Generating bill...";
    }
    const payload = {
        items: [...cart.values()].map(item => ({
            id: item.id,
            quantity: item.quantity,
            modifier_ids: item.modifier_ids || [],
            note: item.note || ""
        })),
        discount: document.getElementById("discount")?.value || "0",
        customer_id: customerId,
        payment_method: paymentMethod,
        note: (() => {
            const rawNote = document.getElementById("billNote")?.value.trim() || "";
            if (currentOrderType === "Takeaway" && !rawNote.toLowerCase().includes("takeaway")) {
                return rawNote ? `[Takeaway] ${rawNote}` : "[Takeaway]";
            }
            return rawNote;
        })()
    };
    if (payloadPayments) {
        payload.payments = payloadPayments;
    }
    if (paymentMethod === "CASH" && !isSplit) {
        const tenderedInput = document.getElementById("tenderedAmount");
        const tenderedVal = tenderedInput ? tenderedInput.value.trim() : "";
        if (tenderedVal) {
            payload.tendered = tenderedVal;
        }
    }
    try {
        const response = await fetch(getBillCreateUrl(), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken")
            },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Unable to generate this bill.");
        const tokenPart = data.token_number ? ` (Token #${data.token_number})` : "";
        showToast(`Bill #${data.bill_number}${tokenPart} generated successfully`);

        const billId = data.bill_id || data.id;
        if (billId) {
            lastBillId = billId;
            try {
                sessionStorage.setItem("last_bill_id", String(billId));
            } catch (e) {}
        }
        if (data.detail_url) {
            lastBillUrl = data.detail_url;
            try {
                sessionStorage.setItem("last_bill_url", data.detail_url);
            } catch (e) {}
            updateReprintButton();
        }

        // Call print endpoint after save (async-safe, short timeout)
        // On failure show a toast and fall back to the browser print dialog
        if (billId) {
            try {
                const printController = new AbortController();
                const printTimeout = setTimeout(() => printController.abort(), 2500);
                const printResp = await fetch(`/billing/${billId}/print/`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({ is_duplicate: false }),
                    signal: printController.signal
                });
                clearTimeout(printTimeout);
                const printData = await printResp.json();
                if (printResp.ok && printData.success) {
                    showToast(`Bill #${data.bill_number} sent to thermal printer`);
                } else {
                    throw new Error(printData.error || "Thermal print failed");
                }
            } catch (printErr) {
                const reason = printErr.name === "AbortError" ? "printer timeout" : (printErr.message || "printer error");
                console.warn("Thermal print failed, falling back to browser print:", printErr);
                showToast(`Thermal printer: ${reason}. Opening browser print dialog...`);
                if (data.detail_url) {
                    printReceipt(data.detail_url);
                }
            }
        } else if (data.detail_url) {
            printReceipt(data.detail_url);
        }
        cart.clear();
        selectedCartId = null;
        const discountEl = document.getElementById("discount");
        if (discountEl) discountEl.value = "0";
        const noteEl = document.getElementById("billNote");
        if (noteEl) noteEl.value = "";
        const customerEl = document.getElementById("customer");
        if (customerEl) customerEl.value = "";
        const customerSearchEl = document.getElementById("customerSearch");
        if (customerSearchEl) customerSearchEl.value = "";
        const selectedCustomerEl = document.getElementById("selectedCustomer");
        if (selectedCustomerEl) selectedCustomerEl.textContent = "Walk-in customer";
        const customerSuggestions = document.getElementById("customerSuggestions");
        if (customerSuggestions) customerSuggestions.style.display = "none";
        const tenderedInput = document.getElementById("tenderedAmount");
        if (tenderedInput) tenderedInput.value = "";
        if (isSplit) {
            const splitToggle = document.getElementById("splitPaymentToggle");
            if (splitToggle) splitToggle.checked = false;
            toggleSplitPayment(false);
        }
        renderCart();
        toggleCartSheet(false);
        const shortcutInput = document.getElementById("shortcutInput");
        if (shortcutInput) {
            shortcutInput.value = "";
            shortcutInput.focus();
        }
    } catch (requestError) {
        if (error) {
            error.textContent = requestError.message;
            error.style.display = "block";
        }
    } finally {
        if (generateButton) {
            generateButton.classList.remove("loading");
            generateButton.textContent = "Generate bill";
            updateGenerateButtonState();
        }
    }
}

function selectCustomer(id, name) {
    const customerEl = document.getElementById("customer");
    if (customerEl) customerEl.value = id;
    const customerSearchEl = document.getElementById("customerSearch");
    if (customerSearchEl) customerSearchEl.value = name;
    const selectedCustomerEl = document.getElementById("selectedCustomer");
    if (selectedCustomerEl) selectedCustomerEl.textContent = `Selected: ${name}`;
    const customerSuggestions = document.getElementById("customerSuggestions");
    if (customerSuggestions) customerSuggestions.style.display = "none";
}

function filterProducts() {
    const searchEl = document.getElementById("productSearch");
    const query = searchEl ? searchEl.value.trim().toLowerCase() : "";
    const activeTab = document.querySelector(".category-tab.active");
    const category = activeTab ? activeTab.dataset.category : "all";
    document.querySelectorAll(".billing-product").forEach((item) => {
        const matchesCategory = category === "all" || item.dataset.category === category;
        const matchesQuery = !query || item.dataset.name.toLowerCase().includes(query);
        item.style.display = matchesCategory && matchesQuery ? "flex" : "none";
    });
}

function clearShortcutBuffer() {
    shortcutBuffer = "";
    if (shortcutTimer) {
        clearTimeout(shortcutTimer);
        shortcutTimer = null;
    }
}

function commitShortcut() {
    const code = shortcutBuffer;
    clearShortcutBuffer();
    if (code) addShortcutCode(code);
}

function handleShortcutCharacter(character) {
    const candidate = shortcutBuffer + character;
    if (shortcutBuffer && shortcutProducts[candidate]) {
        shortcutBuffer = candidate;
        commitShortcut();
        return;
    }
    if (shortcutBuffer && shortcutProducts[shortcutBuffer]) {
        commitShortcut();
        shortcutBuffer = character;
        shortcutTimer = setTimeout(commitShortcut, 500);
        return;
    }
    if (shortcutBuffer) clearShortcutBuffer();
    shortcutBuffer = character;
    shortcutTimer = setTimeout(commitShortcut, 500);
}

async function fetchHeldOrders() {
    try {
        const response = await fetch(getHeldListUrl());
        if (!response.ok) return;
        const data = await response.json();
        const orders = data.orders || [];
        const count = typeof data.count === "number" ? data.count : orders.length;

        const chipLabel = document.getElementById("heldOrdersChipLabel");
        if (chipLabel) chipLabel.textContent = `Held (${count})`;

        const countBadge = document.getElementById("heldDrawerCount");
        if (countBadge) countBadge.textContent = count;

        const listContainer = document.getElementById("heldOrdersList");
        if (listContainer) {
            if (!orders.length) {
                listContainer.innerHTML = '<div class="cart-empty" style="text-align:center;padding:40px 10px;">No held orders.<br><small class="muted">Hold an active bill to resume it later.</small></div>';
            } else {
                listContainer.innerHTML = "";
                orders.forEach(order => {
                    const card = document.createElement("div");
                    card.className = "held-order-card";
                    card.innerHTML = `
                        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
                            <strong style="font-size:16px;">${escapeHtml(order.label || "Order #" + order.id)}</strong>
                            <span style="font-size:16px;font-weight:900;color:var(--billing-coral);">₹${order.total}</span>
                        </div>
                        <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--billing-muted);margin-bottom:8px;">
                            <span>${order.item_count} items</span>
                            <span>${escapeHtml(order.created_at)}</span>
                        </div>
                        ${order.customer_name ? `<div style="font-size:12px;margin-bottom:10px;font-weight:700;">Customer: ${escapeHtml(order.customer_name)}</div>` : ''}
                        <div style="display:flex;gap:8px;">
                            <button type="button" class="btn btn-primary" style="flex:1;min-height:44px;font-size:13px;font-weight:800;" onclick="recallHeldOrder(${order.id})">Recall</button>
                            <button type="button" class="btn btn-outline" style="min-height:44px;padding:0 12px;font-size:13px;font-weight:800;color:var(--billing-danger);border-color:var(--billing-danger);" onclick="discardHeldOrder(${order.id})">Discard</button>
                        </div>
                    `;
                    listContainer.appendChild(card);
                });
            }
        }
    } catch (err) {
        console.error("Failed to fetch held orders:", err);
    }
}

function toggleHeldDrawer(forceOpen) {
    const drawer = document.getElementById("heldDrawer");
    const backdrop = document.getElementById("heldDrawerBackdrop");
    if (!drawer) return;
    const shouldOpen = typeof forceOpen === "boolean" ? forceOpen : !drawer.classList.contains("open");
    if (shouldOpen) {
        drawer.classList.add("open");
        if (backdrop) backdrop.classList.add("active");
        fetchHeldOrders();
    } else {
        drawer.classList.remove("open");
        if (backdrop) backdrop.classList.remove("active");
    }
}

async function holdCurrentOrder() {
    if (!cart.size) {
        showToast("Cart is empty. Nothing to hold.");
        return;
    }
    const label = prompt("Optional label for held order (e.g. Table 2):");
    if (label === null) return;

    const payload = {
        label: label.trim(),
        items: [...cart.values()].map(item => ({
            id: item.id,
            name: item.name,
            price: item.price,
            quantity: item.quantity
        })),
        discount: document.getElementById("discount")?.value || "0",
        customer_id: document.getElementById("customer")?.value || null
    };

    try {
        const response = await fetch(getHeldHoldUrl(), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken")
            },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to hold order");

        cart.clear();
        selectedCartId = null;
        const discountEl = document.getElementById("discount");
        if (discountEl) discountEl.value = "0";
        selectCustomer("", "Walk-in customer");
        renderCart();
        toggleCartSheet(false);
        showToast("Order held successfully");
        fetchHeldOrders();
    } catch (err) {
        showToast(err.message || "Failed to hold order");
    }
}

async function recallHeldOrder(orderId) {
    const hasItems = cart.size > 0;
    let replace = false;
    if (hasItems) {
        if (!confirm("Your current cart has items. Replace it with the held order?")) {
            return;
        }
        replace = true;
    }

    try {
        const response = await fetch(`/pos/billing/held/${orderId}/recall/`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken")
            },
            body: JSON.stringify({ cart_has_items: hasItems, replace: replace })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to recall order");

        const order = data.order;
        cart.clear();
        selectedCartId = null;

        if (Array.isArray(order.items)) {
            for (const item of order.items) {
                const prod = products[item.id] || {
                    id: Number(item.id),
                    name: item.name || "Item " + item.id,
                    price: Number(item.price || 0),
                    stock: 9999,
                    tracked: false,
                    category: ""
                };
                const qty = parseFloat(item.quantity) || 1;
                cart.set(String(item.id), { ...prod, quantity: qty });
            }
        }

        const discountEl = document.getElementById("discount");
        if (discountEl) discountEl.value = order.discount || "0";

        if (order.customer_id && order.customer_name) {
            selectCustomer(order.customer_id, order.customer_name);
        } else {
            selectCustomer("", "Walk-in customer");
        }

        renderCart();
        toggleHeldDrawer(false);
        showToast(`Recalled ${order.label || "Order #" + order.id}`);
        fetchHeldOrders();
    } catch (err) {
        showToast(err.message || "Failed to recall order");
    }
}

async function discardHeldOrder(orderId) {
    if (!confirm("Are you sure you want to discard this held order?")) return;

    try {
        const response = await fetch(`/pos/billing/held/${orderId}/discard/`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken")
            }
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Failed to discard order");

        showToast("Held order discarded");
        fetchHeldOrders();
    } catch (err) {
        showToast(err.message || "Failed to discard order");
    }
}

function initBillingPage() {
    try {
        lastBillUrl = sessionStorage.getItem("last_bill_url") || null;
    } catch (e) {
        lastBillUrl = null;
    }

    document.querySelectorAll(".billing-product").forEach((button) => {
        let mods = [];
        if (button.dataset.modifiers) {
            try {
                mods = JSON.parse(button.dataset.modifiers);
            } catch (e) {
                mods = [];
            }
        }
        const product = {
            id: Number(button.dataset.id),
            name: button.dataset.name,
            price: Number(button.dataset.price),
            stock: Number(button.dataset.stock || 0),
            tracked: button.dataset.tracked === "true",
            category: button.dataset.category,
            modifiers: mods
        };
        products[button.dataset.id] = product;
        if (button.dataset.shortcut) {
            shortcutProducts[button.dataset.shortcut.toUpperCase()] = product;
        }
    });

    document.querySelectorAll(".favorite-item-btn").forEach((button) => {
        const id = button.dataset.id;
        if (products[id] && button.dataset.modifiers && (!products[id].modifiers || !products[id].modifiers.length)) {
            try {
                products[id].modifiers = JSON.parse(button.dataset.modifiers);
            } catch (e) {}
        }
    });

    const shortcutInput = document.getElementById("shortcutInput");
    if (shortcutInput) {
        shortcutInput.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            const code = shortcutInput.value;
            addShortcutCode(code);
            shortcutInput.value = "";
        });
    }

    const customerSearch = document.getElementById("customerSearch");
    const customerSuggestions = document.getElementById("customerSuggestions");
    if (customerSearch && customerSuggestions) {
        customerSearch.addEventListener("input", async () => {
            const query = customerSearch.value.trim();
            const customerEl = document.getElementById("customer");
            if (customerEl) customerEl.value = "";
            const selectedCust = document.getElementById("selectedCustomer");
            if (selectedCust) selectedCust.textContent = "Walk-in customer";
            if (!query) {
                customerSuggestions.style.display = "none";
                return;
            }
            customerSuggestions.innerHTML = '<span class="loading-inline">Searching customers...</span>';
            customerSuggestions.style.display = "block";
            try {
                const response = await fetch(`${getCustomerSearchUrl()}?q=${encodeURIComponent(query)}`);
                if (!response.ok) throw new Error("Search unavailable");
                const data = await response.json();
                customerSuggestions.innerHTML = "";
                if (data.customers && data.customers.length) {
                    data.customers.forEach(customer => {
                        const btn = document.createElement("button");
                        btn.type = "button";
                        btn.className = "btn btn-outline";
                        btn.textContent = customer.name + (customer.phone ? ` - ${customer.phone}` : "");
                        btn.addEventListener("click", () => {
                            selectCustomer(customer.id, customer.name);
                        });
                        customerSuggestions.appendChild(btn);
                    });
                } else {
                    const emptySpan = document.createElement("span");
                    emptySpan.className = "muted";
                    emptySpan.textContent = "No matching customer";
                    customerSuggestions.appendChild(emptySpan);
                }
            } catch (searchError) {
                customerSuggestions.innerHTML = '<span class="muted">Customer search unavailable.</span>';
            }
        });
    }

    document.querySelectorAll(".category-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".category-tab").forEach((item) => item.classList.remove("active"));
            tab.classList.add("active");
            filterProducts();
        });
    });

    const productSearch = document.getElementById("productSearch");
    if (productSearch) {
        productSearch.addEventListener("input", filterProducts);
    }

    document.querySelectorAll('input[name="payment_method"]').forEach((radio) => {
        radio.addEventListener("change", updatePaymentMethodUI);
    });

    document.body.classList.add("pos-billing-mode");
    initFeedback();
    updatePaymentMethodUI();
    updateReprintButton();
    renderCart();
    fetchHeldOrders();
    updateBillingOnlineStatus();

    if (shortcutInput) {
        shortcutInput.focus();
    }
}

function setPaymentMethodByCode(methodCode) {
    const radio = document.querySelector(`input[name="payment_method"][value="${methodCode}"]`);
    if (radio) {
        radio.checked = true;
        updatePaymentMethodUI();
        showToast(`Payment: ${methodCode}`);
    }
}

function cyclePaymentMethod() {
    const methods = ["CASH", "UPI", "CARD", "KHATA"];
    const currentRadio = document.querySelector('input[name="payment_method"]:checked');
    let currentVal = currentRadio ? currentRadio.value : "CASH";
    let nextIdx = (methods.indexOf(currentVal) + 1) % methods.length;
    setPaymentMethodByCode(methods[nextIdx]);
}

// Global Keydown Shortcut Listener
document.addEventListener("keydown", (event) => {
    // Function keys (F2, F4, F8) work everywhere across the terminal
    if (event.key === "F2") {
        event.preventDefault();
        const search = document.getElementById("productSearch") || document.getElementById("shortcutInput");
        if (search) {
            search.focus();
            if (search.select) search.select();
        }
        return;
    }
    if (event.key === "F4") {
        event.preventDefault();
        holdCurrentOrder();
        return;
    }
    if (event.key === "F8") {
        event.preventDefault();
        cyclePaymentMethod();
        return;
    }

    const target = event.target;
    const isInput = target.matches("input, textarea, select, [contenteditable='true']");

    if (event.key === "Enter") {
        // Allow normal Enter handling in shortcut code and customer search inputs
        if (target.id === "shortcutInput" || target.id === "customerSearch") {
            return;
        }
        if (!isInput || event.ctrlKey) {
            event.preventDefault();
            saveBill();
            return;
        }
    }

    if (isInput) return;

    // Number keys 1-4 for instant one-tap payment method selection
    if (event.key === "1") {
        setPaymentMethodByCode("CASH");
        return;
    }
    if (event.key === "2") {
        setPaymentMethodByCode("UPI");
        return;
    }
    if (event.key === "3") {
        setPaymentMethodByCode("CARD");
        return;
    }
    if (event.key === "4") {
        setPaymentMethodByCode("KHATA");
        return;
    }

    if (event.ctrlKey && /^[a-zA-Z0-9]$/.test(event.key)) {
        const product = shortcutProducts[event.key.toUpperCase()];
        if (product) {
            event.preventDefault();
            addItem(product.id);
            showShortcutFeedback(`Added ${product.name}`);
        }
        return;
    }
    if (event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
        clearShortcutBuffer();
        toggleCartSheet(false);
        toggleHeldDrawer(false);
        return;
    }
    if (event.key === "Delete" && selectedCartId) {
        removeItem(selectedCartId);
        return;
    }
    if (event.key === "+" && selectedCartId) {
        changeQuantity(selectedCartId, 1);
        return;
    }
    if (event.key === "-" && selectedCartId) {
        changeQuantity(selectedCartId, -1);
        return;
    }
    if (/^[a-zA-Z0-9]$/.test(event.key)) {
        handleShortcutCharacter(event.key.toUpperCase());
    }
});

// Attach all functions to window for global / inline handlers
window.addItem = addItem;
window.changeQuantity = changeQuantity;
window.updateQuantity = updateQuantity;
window.removeItem = removeItem;
window.clearCart = clearCart;
window.renderCart = renderCart;
window.escapeHtml = escapeHtml;
window.getCookie = getCookie;
window.showShortcutFeedback = showShortcutFeedback;
window.addShortcutCode = addShortcutCode;
window.showToast = showToast;
window.printReceipt = printReceipt;
window.updateReprintButton = updateReprintButton;
window.reprintLastBill = reprintLastBill;
window.getCurrentGrandTotal = getCurrentGrandTotal;
window.updateChangeDue = updateChangeDue;
window.setQuickTendered = setQuickTendered;
window.updatePaymentMethodUI = updatePaymentMethodUI;
window.toggleSplitPayment = toggleSplitPayment;
window.addSplitRow = addSplitRow;
window.removeSplitRow = removeSplitRow;
window.updateSplitAmounts = updateSplitAmounts;
window.updateGenerateButtonState = updateGenerateButtonState;
window.saveBill = saveBill;
window.selectCustomer = selectCustomer;
window.filterProducts = filterProducts;
window.clearShortcutBuffer = clearShortcutBuffer;
window.commitShortcut = commitShortcut;
window.handleShortcutCharacter = handleShortcutCharacter;
window.toggleFeedback = toggleFeedback;
window.toggleCartSheet = toggleCartSheet;
window.fetchHeldOrders = fetchHeldOrders;
window.toggleHeldDrawer = toggleHeldDrawer;
window.holdCurrentOrder = holdCurrentOrder;
window.recallHeldOrder = recallHeldOrder;
window.discardHeldOrder = discardHeldOrder;
window.setCartItemQuantity = setCartItemQuantity;
window.openModifierChooser = openModifierChooser;
window.closeModifierChooser = closeModifierChooser;
window.toggleModifierChip = toggleModifierChip;
window.confirmModifierAdd = confirmModifierAdd;
window.updateBillingOnlineStatus = updateBillingOnlineStatus;
window.setOrderType = setOrderType;
window.setPaymentMethodByCode = setPaymentMethodByCode;
window.cyclePaymentMethod = cyclePaymentMethod;

window.addEventListener("online", updateBillingOnlineStatus);
window.addEventListener("offline", updateBillingOnlineStatus);

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBillingPage);
} else {
    initBillingPage();
}

