const out = document.getElementById("out");
document.querySelectorAll("button[data-tool]").forEach((b) =>
  b.addEventListener("click", async () => {
    out.textContent = "Calling " + b.dataset.tool + " ...";
    try {
      const r = await fetch("/mcp", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "tools/call",
          params: { name: b.dataset.tool, arguments: { customer_country: "DE" } },
        }),
      });
      out.textContent = JSON.stringify(await r.json(), null, 2);
    } catch (e) {
      out.textContent = "Failed: " + e;
    }
  }),
);
