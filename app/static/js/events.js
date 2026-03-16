


function sortEventsByDate(events) {
    if (!Array.isArray(events)) return [];
    return events.sort((a, b) => new Date(a.start) - new Date(b.start));
}

function stripHtml(html) {
   let doc = new DOMParser().parseFromString(html, 'text/html');
   return doc.body.textContent || "";
}

function isSubscriptionActive() {
    return hasActiveSubscription;
}


// Function to calculate age from DOB string (YYYY-MM-DD)
function calculateAge(dobString) {
    const dob = new Date(dobString);
    const today = new Date();
    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();
    // Adjust age if birthday hasn't occurred yet this year
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
        age--;
    }
    return age;
}

// Validation function to check if the guest is 18+
function validateGuestAge() {
    const dobInput = document.getElementById('guestDob');
    const ageErrorDiv = document.getElementById('guestAgeError');
    
    // Reset error message
    if(ageErrorDiv) ageErrorDiv.style.display = 'none';

    if (!dobInput || !dobInput.value) {
        // DOB is required, but let the 'required' attribute handle this
        return false; 
    }

    const dob = dobInput.value;
    const age = calculateAge(dob);

    if (age < 18) {
        const errorMessage = 'You must be 18 years or older to register for an event.';
        if(ageErrorDiv) {
            ageErrorDiv.textContent = errorMessage;
            ageErrorDiv.style.display = 'block';
        } else {
            alert(errorMessage);
        }
        return false;
    }
    return true;
}

/**
 * Formats the date for an event card with special labels for Today and Tomorrow.
 * @param {string} dateString - The ISO date string for the event (e.g., event.start).
 * @returns {string} The formatted date string.
 */

/**
 * Formats the date for an event card with special labels for Today and Tomorrow.
 * @param {string} dateString - The ISO date string for the event (e.g., event.start).
 * @returns {string} The formatted date string.
 */
function formatEventCardDate(dateString) {
    // Get the event date and normalize it to the start of its day
    const eventDate = new Date(dateString);
    eventDate.setHours(0, 0, 0, 0);

    // Get today's date and normalize it
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Calculate tomorrow's date
    const tomorrow = new Date(today);
    tomorrow.setDate(today.getDate() + 1);

    // Define options for formatting the date string (e.g., "Wednesday, August 13")
    const dateOptions = {
        weekday: 'long',
        month: 'long',
        day: 'numeric' // No year, as requested
    };

    // Check if the event is today
    if (eventDate.getTime() === today.getTime()) {
        return `TODAY (${eventDate.toLocaleDateString('en-US', dateOptions)})`;
    }

    // Check if the event is tomorrow
    if (eventDate.getTime() === tomorrow.getTime()) {
        return `TOMORROW (${eventDate.toLocaleDateString('en-US', dateOptions)})`;
    }

    // Otherwise, for any other future date, return the standard format
    return eventDate.toLocaleDateString('en-US', dateOptions);
}

// Helper functions for date/time formatting
function formatEventDate(dateString) {
    const eventDate = new Date(dateString);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const tomorrow = new Date(today.getTime() + 24 * 60 * 60 * 1000);

    const timeString = eventDate.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
    });

    const timeZone = 'PST';

    if (eventDate.getTime() >= today.getTime() && eventDate.getTime() < tomorrow.getTime()) {
        return `Today • ${timeString} ${timeZone}`;
    }
    if (eventDate.getTime() >= tomorrow.getTime() && eventDate.getTime() < tomorrow.getTime() + 24 * 60 * 60 * 1000) {
        return `Tomorrow • ${timeString} ${timeZone}`;
    }

    return `${eventDate.toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
    })} • ${timeString} ${timeZone}`;
}

function formatEventTime(dateString) {
    const eventDate = new Date(dateString);
    return eventDate.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// Add this new function to events.js
function displayFlashMessage(container, message) {
    if (!container || !message) return;

    // Create the alert element
    const alertDiv = document.createElement('div');
    alertDiv.className = 'alert alert-success alert-dismissible fade show';
    alertDiv.setAttribute('role', 'alert');
    alertDiv.style.marginBottom = '15px';

    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    // Add the alert to the top of the specified container
    container.prepend(alertDiv);
}


// Helper to generate the correct action button based on event status
function getActionButtonHTML(event) {
    const capacityAttrs = `data-max-capacity="${event.max_capacity}" data-rsvp-count="${event.rsvp_count}"`;

    if (event.is_past) {
        return `<button class="btn btn-secondary" disabled>Event has passed</button>`;
    }
    if (event.status === 'canceled') {
        return `<button class="btn btn-secondary" disabled>Event Canceled</button>`;
    }
    // 1. Check if the user is attending FIRST.
    if (event.is_attending) {
        return `
            <div class="rsvp-box" style="margin-top: 10px;">
                <p style="font-weight: bold; color: teal; margin: 0;">You're going!</p>
                <a href="#" 
                   class="edit-rsvp" 
                   data-event-id="${event.id}"
                   data-title="${event.title}"
                   data-ticket-price="${event.ticket_price || 0}"
                   data-guest-limit="${event.allow_guests ? (event.guest_limit || 0) : 0}"
                   ${capacityAttrs}
                   style="color: teal; text-decoration: underline;">
                   Edit RSVP
                </a>
            </div>`;
    }
    // 2. If they are not attending, THEN check if it's full.
    if (event.rsvp_count >= event.max_capacity) {
        return `<button class="btn btn-secondary" disabled>Event Full</button>`;
    }
    // Default case: available to attend
    return `
        <button
            class="btn btn-success custom-attend-button"
            data-event-id="${event.id}"
            data-title="${event.title}"
            data-time="${event.formatted_date}, ${formatEventTime(event.start)}"
            data-ticket-price="${event.ticket_price || 0}"
            data-guest-limit="${event.allow_guests ? (event.guest_limit || 0) : 0}"
            data-location="${event.location}"
            ${capacityAttrs}>
            Attend
        </button>`;
}


document.addEventListener('DOMContentLoaded', function () {
    if (typeof subStatus !== 'undefined' && subStatus === 'pending') {
        // Optional: Show a loading indicator (add this HTML to events.html if needed, or use alertDiv from displayFlashMessage)
        const loadingMessage = document.createElement('div');
        loadingMessage.className = 'alert alert-warning';
        loadingMessage.innerHTML = 'Processing subscription... <span class="spinner-border spinner-border-sm" role="status"></span>';
        document.querySelector('#upcoming .tab-pane-body').prepend(loadingMessage);  // Adjust selector to your content area

        let previousCredits = userEventCredits;  // From page_variables in events.html

        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/user/subscription_status');
                if (!response.ok) throw new Error('Polling failed');
                const data = await response.json();

                if (data.event_credits > previousCredits) {
                    clearInterval(pollInterval);
                    loadingMessage.remove();  // Hide loader
                    displayFlashMessage(document.querySelector('#upcoming .tab-pane-body'), 'Subscription activated! Credits added.');
                    location.reload();  // Refresh to update UI
                }
            } catch (error) {
                console.error('Polling error:', error);
                clearInterval(pollInterval);
                loadingMessage.remove();
                alert('Error checking subscription status. Please refresh manually.');
            }
        }, 5000);  // Poll every 5 seconds
    }

    const stickyBar = document.querySelector('.sticky-action-bar');
    const mainContent = document.querySelector('main');

    if (stickyBar && mainContent) {
        // Get the actual height of the sticky bar
        const stickyBarHeight = stickyBar.offsetHeight;
        // Apply that height as padding to the bottom of the main content area, plus some extra space.
        mainContent.style.paddingBottom = `${stickyBarHeight + 20}px`;
    }

    const flashMessage = sessionStorage.getItem('flashMessage');
    const flashEventId = sessionStorage.getItem('flashEventId');

    // Assumes `upcomingEventsData` and `pastEventsData` are available from events.html
    const upcomingTabPane = document.getElementById('upcoming');
    if (upcomingTabPane) {
        // This code will now ONLY run on the main /events page
        const sortedUpcomingEvents = sortEventsByDate(upcomingEventsData);
        const sortedPastEvents = pastEventsData;

        renderEventCards(sortedUpcomingEvents, 'upcoming', flashMessage, flashEventId);
        renderEventCards(sortedPastEvents, 'past');
    }

    const eventDetailsContainer = document.querySelector('.event-details-container');
    // Check if we are on the details page AND if the required `currentEventData` variable exists.
    if (eventDetailsContainer && typeof currentEventData !== 'undefined') {
        // Check if a flash message exists for this specific event
        if (flashMessage && flashEventId && flashEventId == currentEventData.id) {
            // The `displayFlashMessage` function is already in events.js
            displayFlashMessage(eventDetailsContainer, flashMessage);

            // Clean up sessionStorage so the message doesn't appear again
            sessionStorage.removeItem('flashMessage');
            sessionStorage.removeItem('flashEventId');
        }
    }

    // --- Waiver Check for Guest Checkout FIX ---
    // This logic ensures the waiver checkbox is disabled until the link is clicked.
    const waiverLink = document.getElementById('guestWaiverLink');
    const waiverCheckbox = document.getElementById('guestWaiverCheckbox');
    const waiverError = document.getElementById('waiverViewError');
    if (waiverLink) {
            // 1. When the link is clicked, set the viewed flag to true
            waiverLink.addEventListener('click', function() {
                this.setAttribute('data-waiver-viewed', 'true');
                // If the error is showing, hide it, as the user is now viewing the waiver
                if (waiverError) {
                    waiverError.style.display = 'none';
                }
            });
        }

    if (waiverCheckbox) {
        // 2. When the checkbox state changes, check if the waiver was viewed
        waiverCheckbox.addEventListener('change', function() {
            // Check the status from the custom HTML attribute
            const hasViewed = waiverLink ? waiverLink.getAttribute('data-waiver-viewed') === 'true' : false;

            if (this.checked && !hasViewed) {
                // User is trying to check the box but hasn't viewed the link
                this.checked = false; // Prevent the box from being checked
                if (waiverError) {
                    waiverError.style.display = 'block'; // Show the red error message
                }
            } else if (waiverError) {
                // The check was successful (or user is unchecking), hide the error
                waiverError.style.display = 'none'; 
            }
        });
    }

    document.querySelectorAll('.dropdown-toggle').forEach(button => {
        button.addEventListener('click', function(event) {
            event.preventDefault();
            event.stopPropagation();
            let menu = this.nextElementSibling;
            document.querySelectorAll('.dropdown-menu.show').forEach(openMenu => {
                if (openMenu !== menu) openMenu.classList.remove('show');
            });
            menu.classList.toggle('show');
        });
    });

    window.addEventListener('click', function(event) {
        if (!event.target.matches('.dropdown-toggle')) {
            document.querySelectorAll('.dropdown-menu.show').forEach(openMenu => {
                openMenu.classList.remove('show');
            });
        }
    });
    const tabs = document.querySelectorAll('.nav-tabs .nav-link');
    const tabPanes = document.querySelectorAll('.tab-pane');
    let calendarInitialized = false;
    let calendar;

    tabs.forEach(tab => {
        tab.addEventListener('click', function (e) {
            e.preventDefault();
            tabs.forEach(t => t.classList.remove('active'));
            tabPanes.forEach(p => p.classList.remove('active', 'show'));
            this.classList.add('active');
            const activePaneId = this.getAttribute('href').substring(1);
            const activePane = document.getElementById(activePaneId);
            activePane.classList.add('active');
            setTimeout(() => activePane.classList.add('show'), 10);

            if (activePaneId === 'calendar-view') {
                if (!calendarInitialized) {
                    initializeCalendar();
                    calendar.render();
                    calendarInitialized = true;
                } else {
                    calendar.updateSize();
                }
            }
        });
    });

    const defaultTab = document.querySelector('.nav-tabs .nav-link.active');
    if (defaultTab) {
        const defaultPaneId = defaultTab.getAttribute('href').substring(1);
        document.getElementById(defaultPaneId).classList.add('active', 'show');
    }

    const closeAuthPromptButton = document.getElementById('closeAuthPrompt');
    if (closeAuthPromptButton) {
        closeAuthPromptButton.addEventListener('click', function() {
            document.getElementById('authPromptModal').style.display = 'none';
            // Reset the guest section to hidden when closing
            const guestSection = document.getElementById('guestCheckoutSection');
            const authText = document.getElementById('authModalBodyText');
            
            if (guestSection) guestSection.style.display = 'none'; // <--- Reset
            if (authText) authText.textContent = "You need an account to sign up for events or subscriptions."; // <--- Reset text
        });
    }

    function initializeCalendar() {
        const calendarEl = document.getElementById('calendar');
        if (!calendarEl) return;
        calendar = new FullCalendar.Calendar(calendarEl, {
            initialView: 'dayGridMonth',
            events: calendarEventsData, // Use combined data for calendar
            selectable: userRole === 'admin',
            dateClick: function (info) {
                if (userRole === 'admin') openCreateEventModal(info.dateStr);
            },
            headerToolbar: { left: 'prev,next', center: 'title', right: '' },
        });
    }

    // Admin-specific functionality:
    if (userRole === 'admin') {
        const addEventButton = document.getElementById('addEventButton');
        if (addEventButton) {
            addEventButton.addEventListener('click', function () {
                openCreateEventModal(); // Open modal for a new event
            });
        }

        let quill, quillEdit;

        // Initialize Quill for the "Add Event" Modal
        const quillEditor = document.getElementById('quillEditor');
        if (quillEditor) {
            quill = new Quill('#quillEditor', {
                theme: 'snow',
                placeholder: 'Enter the event description here...',
            });
        }

        // Initialize Quill for the "Edit Event" Modal
        const editDescriptionEditor = document.getElementById('editDescriptionEditor');
        if (editDescriptionEditor) {
            quillEdit = new Quill('#editDescriptionEditor', {
                theme: 'snow',
                placeholder: 'Write the event description...',
            });
        }


        // Initialize Quill for the "Add Recurring Event" Modal
        let recurringQuill;
        const recurringQuillEditor = document.getElementById('recurringQuillEditor');
        if (recurringQuillEditor) {
            recurringQuill = new Quill('#recurringQuillEditor', {
                theme: 'snow',
                placeholder: 'Add a description for your event series...'
            });
        }

        // Open the modal for creating events (Admin functionality)
        function openCreateEventModal(date = null, event = null) {
            const modal = document.getElementById('createEventModal');
            const eventDateInput = document.getElementById('date');
            const eventStartTimeInput = document.getElementById('startTime');
            const eventEndTimeInput = document.getElementById('endTime');
            const eventTitleInput = document.getElementById('title');
            const eventDescriptionInput = document.getElementById('description');
            const eventLocationInput = document.getElementById('location');

            modal.style.display = 'block';

            if (date) {
                eventDateInput.value = date;
                eventStartTimeInput.value = '12:00';
                eventEndTimeInput.value = '12:00';
            }

            if (event) {
                eventTitleInput.value = event.title;
                eventDescriptionInput.value = event.description;
                eventLocationInput.value = event.location;
                const eventDate = new Date(event.start);
                eventDateInput.value = eventDate.toISOString().split('T')[0];
                eventStartTimeInput.value = event.start.split('T')[1].slice(0, 5) || '12:00';
                eventEndTimeInput.value = event.end.split('T')[1].slice(0, 5) || '12:00';
                eventDateInput.dataset.eventId = event.id;
            }

            document.getElementById('closeModal').onclick = function () {
                modal.style.display = 'none';
            };
        }

        // Handle event form submission for creating events (Admin functionality)


        // Handle the 'Add Event' form submission
        const createEventForm = document.getElementById('createEventForm');
        if (createEventForm && quill) {
            createEventForm.addEventListener('submit', function() {
                // Before the form submits, copy the HTML content from the Quill editor 
                // into the hidden 'descriptionInput' field.
                document.getElementById('descriptionInput').value = quill.root.innerHTML;
            });
        }

        // Handle the "Edit Event" form submission
        const editEventForm = document.getElementById('editEventForm');
        if (editEventForm && quillEdit) {
            editEventForm.addEventListener('submit', function() {
                document.getElementById('editDescriptionInput').value = quillEdit.root.innerHTML;
            });
        }


         // --- Recurring Event Modal Logic ---
        const recurringModal = document.getElementById('createRecurringEventModal');
        const addRecurringBtn = document.getElementById('addRecurringEventButton');
        const closeRecurringBtn = document.getElementById('closeRecurringModal');
        const recurringForm = document.getElementById('createRecurringEventForm');
        
        // Open Modal
        if (addRecurringBtn) {
            addRecurringBtn.onclick = function() {
                if (recurringModal) recurringModal.style.display = 'block';
            }
        }
    
        // Close with 'X' button
        if (closeRecurringBtn) {
            closeRecurringBtn.onclick = function() {
                if (recurringModal) recurringModal.style.display = 'none';
            }
        }
        
        // Handle Recurring Form Submission
        if (recurringForm) {
            recurringForm.addEventListener('submit', function(e) {
                e.preventDefault(); // Stop default form submission
    
                // Update the hidden description input with Quill's content
                const descriptionInput = document.getElementById('recurringDescriptionInput');
                if (recurringQuill) {
                    descriptionInput.value = recurringQuill.root.innerHTML;
                }
    
                const formData = new FormData(this);
                
                // Basic validation
                if (!formData.get('recurring_start_date') || !formData.get('recurring_end_date')) {
                    alert('Please select a start and end date.');
                    return;
                }
                if (formData.getAll('weekdays').length === 0) {
                    alert('Please select at least one day of the week.');
                    return;
                }
    
                // Optional: Add a loading spinner here
                
                fetch("/events/add_recurring", { // Use the direct URL
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // Use sessionStorage to show a flash message on the next page
                        sessionStorage.setItem('flashMessage', data.message);
                        window.location.reload(); // Reload page to see new events
                    } else {
                        alert('Error: ' + data.error);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('An unexpected error occurred.');
                });
            });
        }



        function openEditEventModal(event) {
            const modal = document.getElementById("editEventModal");

            // Dynamically set the form's action URL to the correct endpoint
            const form = document.getElementById("editEventForm");
            form.action = `/events/edit/${event.id}`;

            // Populate all form fields with the event's current data
            document.getElementById("editTitle").value = event.title;
            // Populate the Quill editor with the event's description
            if (quillEdit) {
                quillEdit.root.innerHTML = event.description;
            }

            document.getElementById("editDate").value = event.start.split("T")[0];

            const startTime = new Date(event.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
            const endTime = new Date(event.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

            document.getElementById("editStartTime").value = startTime;
            document.getElementById("editEndTime").value = endTime;
            document.getElementById("editLocation").value = event.location;
            document.getElementById("editFullAddress").value = event.full_address || '';
            document.getElementById("editAllowGuests").checked = event.allow_guests;
            document.getElementById("editGuestLimit").value = event.guest_limit;
            document.getElementById("editTicketPrice").value = event.ticket_price;
            document.getElementById("editMaxCapacity").value = event.max_capacity;

            // Show a preview of the current image
            const previewImage = document.getElementById("editPreviewImage");
            if (event.image_filename) {
                previewImage.src = `/static/images/${event.image_filename}`;
                previewImage.style.display = "block";
            } else {
                previewImage.style.display = "none";
            }

            modal.style.display = "block";
        }

        // Close the Edit Modal (Admin functionality)
        const closeEditModalButton = document.getElementById("closeEditModal");
        if (closeEditModalButton) { // Ensure button exists
            closeEditModalButton.addEventListener("click", function () {
                document.getElementById("editEventModal").style.display = "none";
            });
        }


        document.querySelectorAll('.edit-event').forEach(button => {
            button.addEventListener('click', function () {
                const eventId = this.getAttribute('data-event-id');
                // Combine upcoming and past events to find the one being edited
                const allEvents = upcomingEventsData.concat(pastEventsData);
                const selectedEvent = allEvents.find(e => e.id == Number(eventId));
                
                if (selectedEvent) {
                    openEditEventModal(selectedEvent);
                } else {
                    console.error("Event not found for ID:", eventId);
                }
            });
        });


        // Confirm cancellation with reason (Admin functionality)
        const confirmCancelEventButton = document.getElementById('confirmCancelEvent');
        if (confirmCancelEventButton) { // Ensure button exists
            confirmCancelEventButton.addEventListener('click', function () {
                const eventId = document.getElementById('cancelEventId').value;
                const cancellationReason = document.getElementById('cancelReason').value;

                fetch(`/events/cancel/${eventId}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            cancellation_reason: cancellationReason
                        })
                    })
                    .then(response => response.json())
                    .then(data => {
                        alert('Event canceled successfully');
                        location.reload();
                    })
                    .catch(err => {
                        console.error('Error canceling event:', err);
                        alert('Failed to cancel event');
                    });

                document.getElementById('cancelEventModal').style.display = 'none';
            });
        }

        // Close the cancel modal (Admin functionality)
        const closeCancelModalButton = document.getElementById('closeCancelModal');
        if (closeCancelModalButton) { // Ensure button exists
            closeCancelModalButton.onclick = function () {
                document.getElementById('cancelEventModal').style.display = 'none';
            };
        }
    } // End of if (userRole === 'admin')

    // Add this to handle closing modals when clicking the background
    window.addEventListener('click', function(event) {
        const createModal = document.getElementById('createEventModal');
        const editModal = document.getElementById('editEventModal');
        const cancelModal = document.getElementById('cancelEventModal');
        const rsvpModal = document.getElementById('rsvpModal');
        const recurringModal = document.getElementById('createRecurringEventModal'); // Our new modal
        const confirmationModal = document.getElementById('confirmationModal'); // Add this line

        if (event.target == createModal) createModal.style.display = "none";
        if (event.target == editModal) editModal.style.display = "none";
        if (event.target == cancelModal) cancelModal.style.display = "none";
        if (event.target == rsvpModal) rsvpModal.style.display = "none";
        if (event.target == recurringModal) recurringModal.style.display = "none"; // Handle our new modal
        if (event.target == confirmationModal) confirmationModal.style.display = "none"; 
    });

    // General event action handler (Admin functionality for delete/cancel) - moved inside admin block
    function handleEventActions(eventId, action, eventData = null) {
        let url = `/events/${action}/${eventId}`;

        fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: eventData ? JSON.stringify(eventData) : null,
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Server returned ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                alert(`${action.charAt(0).toUpperCase() + action.slice(1)} successful.`);
                location.reload();
            })
            .catch(err => {
                console.error(`Action ${action} failed:`, err);
                alert(`Error during ${action}: ${err.message}`);
            });
    }



// Renders event cards into the appropriate container
function renderEventCards(eventsToRender, containerType, flashMessage, flashEventId) {
    const containerSelector = containerType === 'past' ? '.past-events-container' : '.events-container';
    const eventsContainers = document.querySelectorAll(containerSelector);

    if (eventsContainers.length === 0) {
        console.error(`Container not found for type: ${containerType}`);
        return;
    }

    eventsContainers.forEach(eventsContainer => {
        // Clear the container only on the first render to avoid duplication
        if (!eventsContainer.dataset.rendered) {
            eventsContainer.innerHTML = '';
            eventsContainer.dataset.rendered = 'true';
        }

        if (eventsToRender.length === 0) {
            const message = containerType === 'past' ? 'No recent past events to show.' : 'No upcoming events. Check back soon!';
            eventsContainer.innerHTML = `<p>${message}</p>`;
            return;
        }

        eventsToRender.forEach(event => {
            const isCanceled = event.status === 'canceled';
            const cancellationReason = event.cancellation_reason || 'No reason provided';
            let descriptionPreview = 'No description available.';
            if (event.description) {
                const plainText = stripHtml(event.description); // Strip HTML tags first
                if (plainText.length > 12) {
                    descriptionPreview = plainText.substring(0, 12) + '...'; // Truncate to 120 characters
                } else {
                    descriptionPreview = plainText;
                }
            }


            const actionButtonHTML = getActionButtonHTML(event);

            const eventCardHTML = `
            <div class="event-card ${isCanceled ? 'canceled' : ''}" data-event-id="${event.id}">
                <div class="event-header">
                    <p class="event-date">
                        ${isCanceled
                            ? `<strong style="color: red;">${formatEventCardDate(event.start)} - Cancelled: ${cancellationReason}</strong>`
                            : formatEventCardDate(event.start)}
                    </p>
                    <h2 class="event-title">${event.title}</h2>
                    <p class="event-times">
                        <strong>Time:</strong>
                        ${formatEventTime(event.start)} - ${formatEventTime(event.end)}
                    </p>
                    ${userRole === 'admin' && !event.is_past ? `
                        <div class="event-menu">
                            <button class="event-menu-button">⋮</button>
                            <div class="event-menu-options hidden">
                                <button class="edit-event" data-event-id="${event.id}">Edit</button>
                                <button class="delete-event" data-event-id="${event.id}">Delete</button>
                                <button class="cancel-event" data-event-id="${event.id}">Cancel</button>
                            </div>
                        </div>
                    ` : ''}
                </div>
                <div class="event-body">
                    ${event.image_filename ? `
                        <img src="/static/images/${event.image_filename}"
                        alt="Event Image"
                        style="width: 100%; max-height: 200px; object-fit: cover; border-radius: 8px; margin-bottom: 10px;">
                    ` : ''}
                    <p class="event-location"><strong>Where:</strong> ${event.location}</p>
                    <p class="event-description">
                        <strong>Description:</strong> ${descriptionPreview}
                        <a href="/events/${event.id}" class="see-more-button">See More</a>
                    </p>
                    <p class="event-going"><strong>Who's Going:</strong> ${event.rsvp_count || 0} going</p>
                    ${actionButtonHTML}
                </div>
            </div>`;
            
            eventsContainer.insertAdjacentHTML('beforeend', eventCardHTML);
        });
    });
    if (flashMessage && flashEventId) {
    const cardToShowMessageOn = document.querySelector(`.event-card[data-event-id="${flashEventId}"]`);
    if (cardToShowMessageOn) {
        displayFlashMessage(cardToShowMessageOn, flashMessage);
        sessionStorage.removeItem('flashMessage');
        sessionStorage.removeItem('flashEventId');
    }
}
}

    // Central function to attach all event listeners
    function attachAllEventListeners() {

        if (userRole === 'admin') {
            // The buggy '.edit-event' listener has been removed.

            document.querySelectorAll('.delete-event').forEach(button => {
                button.addEventListener('click', function () {
                    const eventId = this.getAttribute('data-event-id');
                    handleEventActions(eventId, 'delete');
                });
            });

            document.querySelectorAll('.cancel-event').forEach(button => {
                button.addEventListener('click', function () {
                    const eventId = this.getAttribute('data-event-id');
                    document.getElementById('cancelEventId').value = eventId;
                    document.getElementById('cancelEventModal').style.display = 'block';
                });
            });
        }


        // RSVP Modal Elements
        const rsvpModal = document.getElementById('rsvpModal');
        const rsvpTitle = document.getElementById('rsvpTitle');
        const rsvpEventInfo = document.getElementById('rsvpEventInfo');
        const guestCountSpan = document.getElementById('guestCount');
        const totalPriceSpan = document.getElementById('totalPriceSpan');
        const paypalContainer = document.getElementById('paypal-container');
        const guestDecrementBtn = document.getElementById('guestDecrement');
        const guestIncrementBtn = document.getElementById('guestIncrement');
        const editGuestPrompt = document.getElementById('editGuestPrompt');
        const capacityInfo = document.getElementById('capacityInfo'); // Get the new element
        const notGoingContainer = document.getElementById('notGoingContainer');
        const notGoingBtn = document.getElementById('notGoingBtn');
        const updateGuestContainer = document.getElementById('updateGuestContainer');


        const updateGuestsBtn = document.getElementById('updateGuestsBtn');

        if (updateGuestsBtn) {
            updateGuestsBtn.addEventListener('click', async () => {
                updateGuestsBtn.disabled = true;
                updateGuestsBtn.innerHTML = '<i class="bi bi-person-plus"></i> Saving...';

                const newGuestCount = parseInt(guestCountSpan.textContent);

                try {
                    const response = await fetch('/api/rsvp/update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            event_id: currentEventId,
                            new_guest_count: newGuestCount
                        })
                    });

                    const result = await response.json();
                    if (response.ok) {
                        sessionStorage.setItem('flashMessage', result.message);
                        sessionStorage.setItem('flashEventId', currentEventId);
                        window.location.reload();
                    } else {
                        alert(`Error: ${result.error || 'Could not update RSVP.'}`);
                        updateGuestsBtn.disabled = false;
                        updateGuestsBtn.innerHTML = '<i class="bi bi-person-plus"></i> Update Guests';
                    }
                } catch (error) {
                    console.error('Error updating RSVP:', error);
                    alert('An unexpected network error occurred.');
                    updateGuestsBtn.disabled = false;
                    updateGuestsBtn.innerHTML = '<i class="bi bi-person-plus"></i> Update Guests';
                }
            });
        }

        let currentTicketPrice = 0;
        let currentGuestLimit = 0;
        let currentEventId = null; // Store the event ID being processed
        let isEditingRsvp = false; // Flag to differentiate initial RSVP from edit
        let initialGuestCount = null;
        let currentSpotsLeft = 0;

        // New function to enable/disable the guest buttons based on capacity ---
        function updateGuestButtonsState(currentGuestCount, spotsLeft, guestLimit) {
            const userAndGuests = 1 + currentGuestCount;
            // Disable '+' if adding a guest would exceed total spots OR the user's guest limit.
            guestIncrementBtn.disabled = userAndGuests >= spotsLeft || (guestLimit > 0 && currentGuestCount >= guestLimit);
            // Disable '-' if guest count is 0.
            guestDecrementBtn.disabled = currentGuestCount <= 0;
        }

        // Function to update total price and re-render PayPal buttons
        function updatePriceAndPayPal(eventId, guestsSelected) {
            let totalAmount;
            let message = ''; // This will hold user-facing messages

            // --- Main Logic Starts Here ---

            // SCENARIO 1: User is editing an existing RSVP.
            if (isEditingRsvp) {
                // For edits, calculate the cost of *additional* guests only.
                const guestDifference = guestsSelected - initialGuestCount;
                totalAmount = currentTicketPrice * guestDifference;
            }
            // SCENARIO 2: This is a brand new RSVP.
            else {
                // CHECK CENTRALIZED CREDITS
                if (userEventCredits > 0) {
                    const remainingBalance = userEventCredits - 1;

                    // Restored Messaging: Shows exact balance deduction
                    message = `
                        <div style="text-align: left; line-height: 1.4; width: 100%;">
                            <p style="margin-bottom: 5px;">Your spot will be covered by 1 Event Credit.</p>
                            <p style="margin: 0; font-size: 0.9em; color: #333;">
                                <strong>Credit Balance:</strong> ${userEventCredits} &rarr; ${remainingBalance}
                            </p>
                        </div>
                    `;
                    // Set total to 0 for the user (pay only for guests)
                    // Logic: User uses 1 credit, pays for guests
                    totalAmount = currentTicketPrice * guestsSelected;
                } else {
                    totalAmount = currentTicketPrice * (1 + guestsSelected);
                }
            }

            // Ensure the displayed price is never negative (handles guest removal in edits).
            const displayPrice = Math.max(0, totalAmount);
            document.getElementById('totalPriceSpan').textContent = displayPrice.toFixed(2);
            
            // Display the relevant message (e.g., "Your spot is free!").
            document.getElementById('rsvp-action-container').innerHTML = message;
            
            // This new function decides whether to show PayPal or a "Confirm Free RSVP" button.
            renderActionButtons(eventId, totalAmount);
        }


        // You still need this function from the previous answer in events.js
        function renderActionButtons(eventId, totalAmount) {
            const paypalContainer = document.getElementById('paypal-container');
            paypalContainer.innerHTML = ''; // Clear previous buttons

            // If there is a cost, render the PayPal buttons
            if (totalAmount > 0) {
                // The message is already set, so we just call your existing PayPal button renderer
                renderPayPalButtons(eventId, totalAmount);
            }
            // If it's a free event redemption (new RSVP with 0 guests)
            else if (!isEditingRsvp && userEventCredits > 0) {
                const freeRsvpButton = document.createElement('button');
                freeRsvpButton.textContent = `Confirm & Use 1 Credit`;
                freeRsvpButton.className = 'btn btn-success';
                paypalContainer.appendChild(freeRsvpButton);

                freeRsvpButton.addEventListener('click', async () => {
                    freeRsvpButton.disabled = true;
                    freeRsvpButton.textContent = 'Processing...';

                    const response = await fetch('/api/rsvp/credit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ event_id: eventId })
                    });

                    if (response.ok) {
                        const result = await response.json();
                        sessionStorage.setItem('flashMessage', result.message);
                        sessionStorage.setItem('flashEventId', eventId);
                        window.location.reload();
                    } else {
                        const result = await response.json();
                        alert(`Error: ${result.error || 'Could not complete registration.'}`);
                        freeRsvpButton.disabled = false; // Re-enable on error
                    }
                });
            }
            else {
                paypalContainer.innerHTML = '<p style="text-align: center; font-weight: bold; color: #333;">No payment required for this change.</p>';
            }
        }

        // Function to render PayPal buttons
        function renderPayPalButtons(eventId, totalAmount) {
            paypalContainer.innerHTML = ''; // Clear previous buttons

            // Only render if totalAmount is positive or it's an edit with potential refund
            // For simplicity, we'll render if totalAmount >= 0 (meaning no new payment or a payment of 0 for refund scenario)
            // The backend will handle if a payment is actually needed.
            if (totalAmount > 0 || (isEditingRsvp && totalAmount !== 0)) {
                paypal.Buttons({
                    createOrder: async function(data, actions) {
                        const guestsSelected = parseInt(guestCountSpan.textContent);
                        const quantity = 1 + guestsSelected; // Attendee + guests

                        try {
                            const response = await fetch("/api/orders", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    event_id: eventId, // Pass event ID
                                    quantity: quantity, // Total number of people (1 + guests)
                                    is_edit: isEditingRsvp, // Tell backend if it's an edit
                                    rsvp_id: isEditingRsvp ? currentRsvpId : null, // Pass RSVP ID if editing
                                    initial_guest_count: isEditingRsvp ? initialGuestCount : 0 // Pass initial guests if editing
                                }),
                            });
                            const orderData = await response.json();
                            if (orderData.error) {
                                alert(orderData.error);
                                throw new Error(orderData.error);
                            }
                            return orderData.id;
                        } catch (error) {
                            console.error("Error creating order:", error);
                            alert("Failed to create PayPal order. Please try again.");
                            return null; // Prevent PayPal from proceeding
                        }
                    },
                    onApprove: async function(data, actions) {
                        try {
                            const response = await fetch(`/api/orders/${data.orderID}/capture`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    event_id: currentEventId, // Use the stored event ID
                                    guest_count: parseInt(guestCountSpan.textContent), // Final guest count
                                    is_edit: isEditingRsvp,
                                    rsvp_id: currentRsvpId,
                                    initial_guest_count: initialGuestCount
                                })
                            });
                            const orderData = await response.json();
                            const transaction = orderData.purchase_units?.[0]?.payments?.captures?.[0];


                            // vvv ADD THIS LOGIC vvv
                            // If a success message exists in the response, store it
                            // If a success message exists, store it AND the event ID
                            if (orderData.success_message) {
                                sessionStorage.setItem('flashMessage', orderData.success_message);
                                sessionStorage.setItem('flashEventId', currentEventId); // <-- ADD THIS LINE
                            }
                            const resultMessage = transaction?.status === "COMPLETED"
                                ? `Transaction completed: ${transaction.id}`
                                : `Transaction failed: ${transaction?.status}`;

                            rsvpModal.style.display = 'none';
                            location.reload(); // Reload to update attendee count and UI
                        } catch (error) {
                            console.error("Error capturing order:", error);
                            alert("Failed to complete PayPal transaction. Please try again.");
                        }
                    },
                    onError: function(err) {
                        console.error("PayPal button error:", err);
                        alert("An error occurred with PayPal. Please try again.");
                    }
                }).render(paypalContainer);
            } else {
        // If there's no cost, show an informational message instead of payment buttons.
                if (isEditingRsvp) {
                    paypalContainer.innerHTML = '<p style="text-align: center; font-weight: bold; color: #333;">Payment is only required for adding guests.</p>';
                }
            }
        }

        // Logic for "Attend" Button (Initial RSVP)
        document.querySelectorAll(".custom-attend-button").forEach(button => {
            button.addEventListener("click", function () {

                if (!isUserAuthenticated) {
                    const authModal = document.getElementById('authPromptModal');
                    const guestSection = document.getElementById('guestCheckoutSection'); // <--- ADD THIS
                    // 2. Show the modal
                    if (authModal) authModal.style.display = 'block';

                    // 3. REVEAL the Guest Section & Update Text (Crucial Step)
                    if (guestSection) guestSection.style.display = 'block'; // <--- THIS WAS MISSING
                    // 4. Capture event data for guest checkout
                    window.pendingGuestEventId = this.getAttribute("data-event-id");
                    window.pendingGuestTicketPrice = this.getAttribute("data-ticket-price");
                    return;
                }
                currentEventId = this.getAttribute("data-event-id");
                const eventTitle = this.getAttribute("data-title");
                const eventTime = this.getAttribute("data-time");
                const eventLocation = this.getAttribute("data-location"); 

                currentTicketPrice = parseFloat(this.getAttribute("data-ticket-price"));
                currentGuestLimit = parseInt(this.getAttribute("data-guest-limit"));

                const maxCapacity = parseInt(this.getAttribute("data-max-capacity"));
                const rsvpCount = parseInt(this.getAttribute("data-rsvp-count"));
                currentSpotsLeft = maxCapacity - rsvpCount;
                const spotsLeftInEvent = maxCapacity - rsvpCount;


                // --- Replacement Code ---
                const spotsAvailableForGuests = currentSpotsLeft - 1;
                const maxGuestsUserCanAdd = Math.min(spotsAvailableForGuests, currentGuestLimit);
                capacityInfo.textContent = `(You can bring up to ${maxGuestsUserCanAdd} guest${maxGuestsUserCanAdd !== 1 ? 's' : ''})`;
                isEditingRsvp = false;
                currentRsvpId = null;
                initialGuestCount = 0; // For new RSVP, initial guests is 0

                rsvpTitle.textContent = `You're booking: ${eventTitle}`;
                rsvpEventInfo.innerHTML = `When: ${eventTime}<br>Where: ${eventLocation}`;
                editGuestPrompt.textContent = `Are you bringing someone? (Max: ${currentGuestLimit})`;
                capacityInfo.textContent = `(${spotsLeftInEvent} spot${spotsLeftInEvent !== 1 ? 's' : ''} left in this event)`;
                if (notGoingContainer) notGoingContainer.style.display = 'none';


                guestCountSpan.textContent = '0';

                updatePriceAndPayPal(currentEventId, 0); // The '0' is for the initial guest count.
                updateGuestButtonsState(0, currentSpotsLeft, currentGuestLimit);


                rsvpModal.style.display = 'block';
            });
        });

        // Logic for "Edit RSVP" Link
        document.querySelectorAll(".edit-rsvp").forEach(link => {
            link.addEventListener("click", async function (e) {
                e.preventDefault(); // Prevent default link behavior
                currentEventId = this.getAttribute("data-event-id");

                isEditingRsvp = true;

                try {
                    const rsvpResponse = await fetch(`/api/user_rsvp/${currentEventId}`);
                    if (!rsvpResponse.ok) throw new Error('Failed to fetch RSVP details');
                    const rsvpData = await rsvpResponse.json();

                    if (!rsvpData || !rsvpData.rsvp_id) {
                        alert("Could not retrieve your current RSVP. Please try again.");
                        return;
                    }
                    
                    currentRsvpId = rsvpData.rsvp_id;
                    initialGuestCount = rsvpData.guest_count || 0;

                    const eventTitle = this.getAttribute("data-title");
                    currentTicketPrice = parseFloat(this.getAttribute("data-ticket-price") || 0);
                    currentGuestLimit = parseInt(this.getAttribute("data-guest-limit") || 0);

                    const maxCapacity = parseInt(this.getAttribute("data-max-capacity"));
                    const rsvpCount = parseInt(this.getAttribute("data-rsvp-count"));
                    const spotsCurrentlyHeldByUser = 1 + initialGuestCount;

                    currentSpotsLeft = (maxCapacity - rsvpCount) + spotsCurrentlyHeldByUser;


                    const personalSlotsLeft = currentGuestLimit - initialGuestCount;

                    // 2. How many spots are actually open in the event?
                    const eventSpotsLeft = maxCapacity - rsvpCount;

                    // 3. The true number of guests a user can add is the MINIMUM of those two values.
                    const maxGuestsUserCanAdd = Math.min(personalSlotsLeft, eventSpotsLeft);

                    // 4. Construct the dynamic message based on the result.
                    let capacityMessage = '';
                    if (maxGuestsUserCanAdd > 0) {
                        const pluralS = maxGuestsUserCanAdd !== 1 ? 's' : '';
                        capacityMessage = `(You can add up to ${maxGuestsUserCanAdd} more guest${pluralS})`;
                    } else {
                        capacityMessage = `(The event is full or you've reached your guest limit)`;
                    }
                    capacityInfo.textContent = capacityMessage;


                    // Now populate the modal
                    rsvpTitle.textContent = `Edit RSVP for: ${eventTitle}`;
                    rsvpEventInfo.innerHTML = `You (${rsvpData.first_name} ${rsvpData.last_name}) are already going with <span id="currentGuestsDisplay">${initialGuestCount}</span> guests.`;
                    editGuestPrompt.textContent = `Change number of guests (Max: ${currentGuestLimit}):`;

                    // Find the action buttons container
                    const editRsvpActions = document.getElementById('edit-rsvp-actions');
                    const notGoingContainer = document.getElementById('notGoingContainer');
                    const updateGuestContainer = document.getElementById('updateGuestContainer');
                    
                    // Show the main actions container and the "Not Going" button by default for edits
                    if (editRsvpActions) editRsvpActions.style.display = 'flex';
                    if (notGoingContainer) notGoingContainer.style.display = 'block';
                    if (updateGuestContainer) updateGuestContainer.style.display = 'none';

                    guestCountSpan.textContent = initialGuestCount;
                    updatePriceAndPayPal(currentEventId, initialGuestCount);

                    updateGuestButtonsState(initialGuestCount, currentSpotsLeft, currentGuestLimit);
                    rsvpModal.style.display = 'block';


                } catch (error) {
                    console.error("Error fetching RSVP for edit:", error);
                    alert("An error occurred while loading your RSVP details. Please try again.");
                }
            });
        });

        // Guest selection logic for the modal (shared by both initial and edit)
        guestDecrementBtn.onclick = function () {
            let count = parseInt(guestCountSpan.textContent);
            if (count > 0) {
                count--;
                guestCountSpan.textContent = count;
                updatePriceAndPayPal(currentEventId, count);
                // When editing, show the 'Change Guests' button only if guest count drops below the initial count.
                if (isEditingRsvp) {
                    notGoingContainer.style.display = 'block';

                    if (count < initialGuestCount) {
                        updateGuestContainer.style.display = 'block';
                    } else {
                        updateGuestContainer.style.display = 'none';
                    }
                }
                updateGuestButtonsState(count, currentSpotsLeft, currentGuestLimit);
            }
        };

        guestIncrementBtn.onclick = function () {
            let count = parseInt(guestCountSpan.textContent);
            const userAndGuests = 1 + count;
            if (userAndGuests < currentSpotsLeft && (currentGuestLimit === 0 || count < currentGuestLimit)) {
                count++;
                guestCountSpan.textContent = count;
                updatePriceAndPayPal(currentEventId, count);
                // When editing, hide the 'Change Guests' button if the count returns to the initial count or higher.
                if (isEditingRsvp) {
                    // Hide "Not Going" button if user increments beyond their original number of guests.
                    if (count > initialGuestCount) {
                        notGoingContainer.style.display = 'none';
                    } else {
                        notGoingContainer.style.display = 'block';
                    }

                    // "Update Guests" button should be hidden if count is not less than initial.
                    if (count < initialGuestCount) {
                        updateGuestContainer.style.display = 'block';
                    } else {
                        updateGuestContainer.style.display = 'none';
                    }
                }
                updateGuestButtonsState(count, currentSpotsLeft, currentGuestLimit);
            }
        };

        
        // --- New Reusable Confirmation Modal Logic ---
        const confirmationModal = document.getElementById('confirmationModal');
        const confirmationMessage = document.getElementById('confirmationMessage');
        const confirmBtn = document.getElementById('confirmBtn');
        const cancelBtn = document.getElementById('cancelBtn');

        let onConfirmCallback = null;

        function showConfirmationModal(message, onConfirm) {
            onConfirmCallback = onConfirm;
            confirmationMessage.textContent = message;
            if (confirmationModal) confirmationModal.style.display = 'block';
            // Reset button state
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = 'Confirm';
        }

        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => {
                // Provide visual feedback and prevent multiple clicks
                confirmBtn.disabled = true;
                confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...';
                
                if (onConfirmCallback) {
                    onConfirmCallback();
                }
            });
        }
        
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                if (confirmationModal) confirmationModal.style.display = 'none';
                onConfirmCallback = null; // Clear callback
            });
        }

        if (notGoingBtn) {
            notGoingBtn.addEventListener('click', function() {
                const message = "This will remove everyone in your rsvp. You and your guests (if any) will not be refunded.";
                
                showConfirmationModal(message, () => {
                    if (!currentEventId) {
                        alert('Error: Event ID not found.');
                        if (confirmationModal) confirmationModal.style.display = 'none';
                        return;
                    }

                    fetch(`/api/rsvp/delete/${currentEventId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            sessionStorage.setItem('flashMessage', data.message);
                            sessionStorage.setItem('flashEventId', currentEventId);
                            window.location.reload();
                        } else {
                            alert('Error: ' + (data.error || 'Could not cancel RSVP.'));
                            // Hide modal on error
                            if (confirmationModal) confirmationModal.style.display = 'none';
                        }
                    })
                    .catch(error => {
                        console.error('Error canceling RSVP:', error);
                        alert('An unexpected error occurred. Please try again.');
                        // Hide modal on error
                        if (confirmationModal) confirmationModal.style.display = 'none';
                    });
                });
            });
        }

        document.getElementById('closeRsvpX').addEventListener('click', function () {
            rsvpModal.style.display = 'none';
        });

        // 1. Auth Selection Modal Logic
        const authModal = document.getElementById('authPromptModal');
        const guestModal = document.getElementById('guestCheckoutModal');
        const btnContinueGuest = document.getElementById('btnContinueGuest');

        // Close buttons for new modals
        document.querySelectorAll('.close-auth-modal').forEach(btn => {
            btn.addEventListener('click', () => { if(authModal) authModal.style.display = 'none'; });
        });
        document.querySelectorAll('.close-guest-modal').forEach(btn => {
            btn.addEventListener('click', () => { if(guestModal) guestModal.style.display = 'none'; });
        });

        // "Continue as Guest" Click Handler
        if (btnContinueGuest) {
            btnContinueGuest.addEventListener('click', function() {
                if(authModal) authModal.style.display = 'none';
                if(guestModal) guestModal.style.display = 'block';
                // Start the guest checkout process using the data we saved in step 1
                initGuestCheckout(window.pendingGuestEventId, window.pendingGuestTicketPrice);
            });
        }

        // Guest Waiver Checkbox Logic
        const guestWaiverLink = document.getElementById('guestWaiverLink');
        const guestWaiverCheckbox = document.getElementById('guestWaiverAgree');
        if (guestWaiverLink && guestWaiverCheckbox) {
            guestWaiverLink.addEventListener('click', () => {
                guestWaiverCheckbox.disabled = false;
                // Optional: automatically check it when they click the link
                // guestWaiverCheckbox.checked = true; 
            });
        }
    }



    // Initial render and attach listeners when DOM is ready
    if (!userRole) {
        userRole = 'user';
    }
    console.log("Updated User Role:", userRole);
    attachAllEventListeners(); 
});


// Guest Checkout Function

function initGuestCheckout(eventId, ticketPrice) {
    let guestCount = 0;
    const priceDisplay = document.getElementById('guestTotalPrice');
    const countDisplay = document.getElementById('guestGuestCount');
    
    // Helper to update UI
    const updatePrice = () => {
        const total = (1 + guestCount) * parseFloat(ticketPrice);
        if(priceDisplay) priceDisplay.textContent = '$' + total.toFixed(2);
        if(countDisplay) countDisplay.textContent = guestCount;
    };
    updatePrice(); // Run once on init

    // Guest Counter Click Handlers
    const decBtn = document.getElementById('guestGuestDecrement');
    const incBtn = document.getElementById('guestGuestIncrement');
    
    if(decBtn) decBtn.onclick = () => {
        if (guestCount > 0) { guestCount--; updatePrice(); }
    };
    if(incBtn) incBtn.onclick = () => {
        guestCount++; updatePrice();
    };

    // Signature Pad Initialization
    // Note: Ensure you have included signature_pad.js in your HTML templates
    const canvas = document.getElementById('guestSignaturePad');
    let signaturePad;
    
    if (canvas) {
        const ratio = Math.max(window.devicePixelRatio || 1, 1);
        canvas.width = canvas.offsetWidth * ratio;
        canvas.height = canvas.offsetHeight * ratio;
        canvas.getContext("2d").scale(ratio, ratio);
        signaturePad = new SignaturePad(canvas);
        
        const clearBtn = document.getElementById('clearGuestSignature');
        if(clearBtn) clearBtn.onclick = () => signaturePad.clear();
    }

    // Render PayPal Buttons
    const container = document.getElementById('guest-paypal-container');
    if (container) {
        container.innerHTML = ''; // Clear any existing buttons

        paypal.Buttons({
            onClick: function(data, actions) {
                // Validation before opening PayPal
                const form = document.getElementById('guestCheckoutForm');
                const waiver = document.getElementById('guestWaiverCheckbox');
                
                if (!form.checkValidity()) {
                    form.reportValidity(); // Shows browser validation errors
                    return actions.reject();
                }

                if (!validateGuestAge()) {
                    // validateGuestAge handles the error message display itself
                    return actions.reject();
                }

                if (!waiver.checked) {
                    alert("You must agree to the waiver.");
                    return actions.reject();
                }
                if (signaturePad && signaturePad.isEmpty()) {
                    alert("Please sign the waiver.");
                    return actions.reject();
                }
                return actions.resolve();
            },
            createOrder: function(data, actions) {
                return fetch('/api/orders', {
                    method: 'post',
                    headers: { 'content-type': 'application/json' },
                    body: JSON.stringify({
                        event_id: eventId,
                        quantity: 1 + guestCount, // You + Guests
                        is_guest_checkout: true
                    })
                }).then(res => res.json()).then(orderData => orderData.id);
            },
            onApprove: function(data, actions) {
                return fetch(`/api/orders/${data.orderID}/capture`, {
                    method: 'post',
                    headers: { 'content-type': 'application/json' },
                    body: JSON.stringify({
                        event_id: eventId,
                        guest_count: guestCount,
                        is_guest_checkout: true,
                        guest_info: {
                            first_name: document.getElementById('guestFirstName').value,
                            last_name: document.getElementById('guestLastName').value,
                            email: document.getElementById('guestEmail').value,
                            address: document.getElementById('guestAddress').value,
                            dob: document.getElementById('guestDob').value,
                            emergency_contact_name: document.getElementById('guestEmergName').value,
                            emergency_contact_phone: document.getElementById('guestEmergPhone').value,
                            signature_data: signaturePad ? signaturePad.toDataURL() : null
                        }
                    })
                }).then(res => res.json()).then(orderData => {
                    if (orderData.error) {
                        alert('Error: ' + orderData.error);
                    } else {
                        const guestModal = document.getElementById('guestCheckoutModal');
                        if(guestModal) guestModal.style.display = 'none';
                        alert(orderData.success_message);
                        location.reload();
                    }
                });
            }
        }).render('#guest-paypal-container');
    }
}