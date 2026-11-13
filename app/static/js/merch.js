document.addEventListener('DOMContentLoaded', () => {
    const CART_KEY = 'pickup_main_merch_cart';
    const MAX_QUANTITY = 10;

    const statusElement =
        document.getElementById('merchStatus');

    const cartCount =
        document.getElementById('merchHeaderCartCount');

    let cart = loadCart();

    function loadCart() {
        try {
            const saved = JSON.parse(
                localStorage.getItem(CART_KEY)
            );

            if (!Array.isArray(saved)) {
                return [];
            }

            return saved.filter(item =>
                item &&
                typeof item.productId === 'string' &&
                typeof item.name === 'string' &&
                typeof item.size === 'string' &&
                typeof item.color === 'string' &&
                Number.isInteger(item.variantId) &&
                Number.isInteger(item.quantity) &&
                item.quantity >= 1 &&
                item.quantity <= MAX_QUANTITY &&
                Number.isFinite(Number(item.unitPrice))
            );
        } catch (error) {
            return [];
        }
    }

    function saveCart() {
        localStorage.setItem(
            CART_KEY,
            JSON.stringify(cart)
        );
    }

    function updateCartCount() {
        const totalItems = cart.reduce(
            (total, item) => total + item.quantity,
            0
        );

        cartCount.textContent = totalItems;
    }

    function setStatus(message, type = '') {
        statusElement.textContent = message;

        statusElement.className =
            `merch-status ${type ? `is-${type}` : ''}`;
    }

    document.querySelectorAll('.merch-image-wrap').forEach(wrap => {
        const slides = Array.from(wrap.querySelectorAll('.merch-image-slide'));
        const dots = Array.from(wrap.querySelectorAll('.merch-gallery-dot'));
        let active = 0;
        let manuallySelected = false;
        let hoverPreview = false;

        function showPhoto(index, manual = false) {
            active = (index + slides.length) % slides.length;
            if (manual) {
                manuallySelected = true;
                hoverPreview = false;
            }
            slides.forEach((slide, position) => {
                const selected = position === active;
                slide.classList.toggle('is-active', selected);
                slide.setAttribute('aria-hidden', String(!selected));
            });
            dots.forEach((dot, position) => {
                const selected = position === active;
                dot.classList.toggle('is-active', selected);
                dot.setAttribute('aria-current', String(selected));
            });
        }

        wrap.querySelector('.is-prev').addEventListener('click', () => {
            showPhoto(active - 1, true);
        });
        wrap.querySelector('.is-next').addEventListener('click', () => {
            showPhoto(active + 1, true);
        });
        dots.forEach(dot => dot.addEventListener('click', () => {
            showPhoto(Number(dot.dataset.index), true);
        }));
        wrap.addEventListener('pointerenter', event => {
            if (event.pointerType === 'mouse' && !manuallySelected && active === 0) {
                hoverPreview = true;
                showPhoto(1);
            }
        });
        wrap.addEventListener('pointerleave', event => {
            if (event.pointerType === 'mouse' && hoverPreview) {
                hoverPreview = false;
                showPhoto(0);
            }
        });
        wrap.addEventListener('keydown', event => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();
                showPhoto(active + (event.key === 'ArrowRight' ? 1 : -1), true);
            }
        });
        wrap.resetGallery = () => {
            manuallySelected = false;
            hoverPreview = false;
            showPhoto(0);
        };
    });

    document
        .querySelectorAll('.merch-variant')
        .forEach(select => {
            const card = select.closest('.merch-product-card');
            const wrap = card.querySelector('.merch-image-wrap');
            const [primary, detail] = wrap.querySelectorAll('.merch-image-slide');
            const defaultImage = primary.getAttribute('src');
            const defaultDetail = detail.getAttribute('src');
            const defaultFocus = detail.style.objectPosition;

            select.addEventListener('change', () => {
                const option = select.selectedOptions[0];
                const available = Number(
                    option?.dataset.available || 0
                );
                const selectedImage = option?.dataset.image || defaultImage;
                primary.src = selectedImage;
                const selectedDetail = option?.dataset.detailImage || defaultDetail;
                detail.src = selectedDetail;
                detail.classList.toggle(
                    'is-zoom',
                    selectedDetail === selectedImage
                );
                detail.style.objectPosition = selectedImage === defaultImage
                    ? defaultFocus
                    : 'center';
                card.dataset.productImage = selectedImage;
                wrap.resetGallery();

                const quantitySelect = card.querySelector('.merch-quantity');
                Array.from(quantitySelect.options).forEach(option => {
                    option.disabled = Number(option.value) > available;
                });
                if (Number(quantitySelect.value) > available) {
                    quantitySelect.value = '1';
                }
            });
        });

    document
        .querySelectorAll('.merch-add-button')
        .forEach(button => {
            button.addEventListener('click', () => {
                const card = button.closest(
                    '.merch-product-card'
                );

                const variantSelect = card.querySelector(
                    '.merch-variant'
                );

                const quantity = Number(
                    card.querySelector(
                        '.merch-quantity'
                    ).value
                );

                const option = variantSelect.selectedOptions[0];

                if (!variantSelect.value) {
                    variantSelect.focus();

                    setStatus(
                        'Choose a color and size before adding this item.',
                        'error'
                    );

                    return;
                }

                const newItem = {
                    productId: card.dataset.productId,
                    variantId: Number(variantSelect.value),
                    name: card.dataset.productName,
                    size: option.dataset.size,
                    color: option.dataset.color,
                    available: Number(option.dataset.available),
                    quantity,
                    unitPrice: Number(
                        card.dataset.productPrice
                    ),
                    image: card.dataset.productImage
                };

                const existing = cart.find(item =>
                    item.variantId === newItem.variantId
                );

                if (quantity + (existing?.quantity || 0) >
                    Number(option.dataset.available)) {
                    setStatus('Not enough of this color and size is in stock.', 'error');
                    return;
                }

                if (existing) {
                    if (
                        existing.quantity + quantity >
                        MAX_QUANTITY
                    ) {
                        setStatus(
                            (
                                `Maximum quantity is ` +
                                `${MAX_QUANTITY} per color and size.`
                            ),
                            'error'
                        );

                        return;
                    }

                    existing.quantity += quantity;
                } else {
                    cart.push(newItem);
                }

                saveCart();
                updateCartCount();

                setStatus(
                    (
                        `${newItem.name} (${newItem.color}, ${newItem.size}) ` +
                        `was added to your cart.`
                    ),
                    'success'
                );

                const originalHtml = button.innerHTML;

                button.innerHTML =
                    '<i class="fa-solid fa-check"></i> Added';

                window.setTimeout(() => {
                    button.innerHTML = originalHtml;
                }, 1200);
            });
        });

    updateCartCount();
});
