/**
 * Application JavaScript Utilities
 */
document.addEventListener("DOMContentLoaded", function () {
    // Mobile sidebar toggle
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebarToggle");
    const sidebarClose = document.getElementById("sidebarClose");

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener("click", function () {
            sidebar.classList.toggle("show");
        });
    }

    if (sidebarClose && sidebar) {
        sidebarClose.addEventListener("click", function () {
            sidebar.classList.remove("show");
        });
    }
});

/**
 * Universal Money Formatter for client-side templates and scripts.
 */
window.formatMoney = function (amount, symbol) {
    const val = parseFloat(amount) || 0;
    const sym = symbol || window.CURRENCY_SYMBOL || (typeof currencySymbol !== "undefined" ? currencySymbol : "Rs.");
    return `${sym} ${val.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
