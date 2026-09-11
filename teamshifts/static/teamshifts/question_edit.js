document.addEventListener("DOMContentLoaded", () => {

    const variant = document.querySelector("#id_variant")
    const options = document.querySelector("#teamshifts-answer-options")

    const toggleOptions = () => {
        const showOptions = ["choices", "choices_dropdown", "multiple_choice"].includes(
            variant.value
            )

        options.style.display = showOptions ? "block" : "none"
    }

    variant.addEventListener("change", toggleOptions)
    toggleOptions()
})
