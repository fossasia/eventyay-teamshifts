function gettext(msgid) {
  return typeof window.gettext === "function" ? window.gettext(msgid) : msgid
}

/**
 * @throws {Error} when the network request fails or server returns non-2xx
 */
async function submitCheckout(form) {
  const response = await fetch(form.action, {
    method: "POST",
    body: new FormData(form),
    headers: { "X-Requested-With": "XMLHttpRequest" },
  })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    const message = (data && data.error) || `Request failed with status ${response.status}`
    throw new Error(message)
  }
  return response.json()
}

function setButtonLoading(button) {
  button.disabled = true
  button.replaceChildren()
  const icon = document.createElement("i")
  icon.className = "fa fa-spinner fa-spin"
  button.appendChild(icon)
}

function setButtonDone(button) {
  button.disabled = true
  button.className = "btn btn-success btn-xs"
  button.replaceChildren()
  const icon = document.createElement("i")
  icon.className = "fa fa-check"
  const label = document.createTextNode(` ${gettext("Checked out")}`)
  button.appendChild(icon)
  button.appendChild(label)
}

function restoreButtonChildren(button, originalChildren) {
  button.replaceChildren(...originalChildren)
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".shift-checkout-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault()
      const button = form.querySelector("button")
      const originalChildren = Array.from(button.childNodes).map((n) => n.cloneNode(true))
      setButtonLoading(button)
      let succeeded = false

      try {
        const data = await submitCheckout(form)
        if (data.status === "ok") {
          setButtonDone(button)
          succeeded = true
        } else {
          restoreButtonChildren(button, originalChildren)
          alert(data.error || gettext("An error occurred."))
        }
      } catch (error) {
        console.error("Checkout failed", error)
        restoreButtonChildren(button, originalChildren)
        alert(error.message || gettext("An error occurred."))
      } finally {
        if (!succeeded) {
          button.disabled = false
        }
      }
    })
  })
})
