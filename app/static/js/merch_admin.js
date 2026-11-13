document.addEventListener('DOMContentLoaded', () => {
    const variants = document.getElementById('merchNewVariants');
    const addButton = document.getElementById('merchAddVariant');
    if (!variants || !addButton) return;

    addButton.addEventListener('click', () => {
        const row = document.createElement('div');
        row.className = 'merch-admin-new-variant';
        row.innerHTML = `
            <label>Color <input name="new_color[]" maxlength="40" placeholder="Teal"></label>
            <label>Size <input name="new_size[]" maxlength="20" placeholder="M or One Size"></label>
            <label>On hand <input name="new_stock[]" type="number" min="0" max="1000000" value="0"></label>
            <label>Color photo <input name="new_image[]" type="file" accept="image/jpeg,image/png,image/webp"></label>
            <button type="button" class="merch-remove-variant">Remove</button>
        `;
        variants.appendChild(row);
        row.querySelector('input').focus();
    });

    variants.addEventListener('click', event => {
        if (event.target.classList.contains('merch-remove-variant')) {
            event.target.closest('.merch-admin-new-variant').remove();
        }
    });
});
