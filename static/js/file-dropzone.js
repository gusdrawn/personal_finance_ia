(() => {
    "use strict";

    const fileDropzones = document.querySelectorAll("[data-file-dropzone]");

    if (!fileDropzones.length) {
        return;
    }

    const formatSize = (bytes) => {
        if (bytes < 1024 * 1024) {
            return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        }
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const hasFiles = (event) => {
        const types = event.dataTransfer?.types;
        return types && Array.from(types).includes("Files");
    };

    const acceptsFile = (file, accept) => {
        if (!accept) {
            return true;
        }

        const name = (file.name || "").toLowerCase();
        const type = (file.type || "").toLowerCase();
        return accept.split(",").some((rawToken) => {
            const token = rawToken.trim().toLowerCase();
            if (!token) {
                return false;
            }
            if (token.startsWith(".")) {
                return name.endsWith(token);
            }
            if (token.endsWith("/*")) {
                return type.startsWith(token.slice(0, -1));
            }
            return type === token;
        });
    };

    const setInputFiles = (input, files) => {
        try {
            const transfer = new DataTransfer();
            files.forEach((file) => transfer.items.add(file));
            input.files = transfer.files;
            return true;
        } catch (_error) {
            return false;
        }
    };

    fileDropzones.forEach((zone) => {
        const input = zone.querySelector("[data-file-input]");
        const title = zone.querySelector("[data-file-title]");
        const hint = zone.querySelector("[data-file-hint]");
        const status = zone.querySelector("[data-file-status]");
        const action = zone.querySelector("[data-file-action]");
        const form = zone.closest("form");
        const clear = form?.querySelector("[data-file-clear]");
        const submit = form?.querySelector("[data-file-submit]");
        const defaultTitle = title?.textContent || "Seleccionar archivo";
        const defaultHint = hint?.textContent || "Haz clic para buscarlo en tu dispositivo";
        const defaultAction = action?.textContent || "Elegir archivo";
        const defaultStatus = status?.textContent || "Ningún archivo seleccionado";
        const accept = zone.dataset.accept || input?.accept || "";
        const maxSize = Number(zone.dataset.maxSize || 0);
        const multiple = Boolean(input?.multiple);
        let dragDepth = 0;

        if (!input) {
            return;
        }

        const showStatus = (message, isError = false) => {
            if (!status) {
                return;
            }
            status.textContent = message;
            status.classList.toggle("is-error", isError);
            status.setAttribute("role", isError ? "alert" : "status");
        };

        const resetDragState = () => {
            dragDepth = 0;
            zone.classList.remove("is-dragging");
        };

        const clearSelection = () => {
            input.value = "";
            zone.classList.remove("is-selected", "is-invalid");
            if (title) title.textContent = defaultTitle;
            if (hint) hint.textContent = defaultHint;
            if (action) action.textContent = defaultAction;
            showStatus(defaultStatus);
            clear?.classList.add("hidden");
        };

        const renderSelection = (files) => {
            if (!files.length) {
                clearSelection();
                return;
            }

            const names = files.map((file) => file.name);
            const visibleNames = names.slice(0, 2).join(" · ");
            const remaining = names.length - 2;
            zone.classList.remove("is-invalid");
            zone.classList.add("is-selected");
            if (title) title.textContent = `${files.length} archivo${files.length === 1 ? "" : "s"} listo${files.length === 1 ? "" : "s"}`;
            if (hint) hint.textContent = remaining > 0 ? `${visibleNames} · +${remaining} más` : visibleNames;
            if (action) action.textContent = "Cambiar";
            showStatus(`${files.length} archivo${files.length === 1 ? "" : "s"} seleccionado${files.length === 1 ? "" : "s"} · ${formatSize(files.reduce((total, file) => total + file.size, 0))}`);
            clear?.classList.remove("hidden");
        };

        const validateFiles = (files) => {
            if (!multiple && files.length > 1) {
                return "Este campo acepta un solo archivo.";
            }
            const invalidType = files.find((file) => !acceptsFile(file, accept));
            if (invalidType) {
                return `${invalidType.name}: formato no permitido.`;
            }
            if (maxSize) {
                const oversized = files.find((file) => file.size > maxSize);
                if (oversized) {
                    return `${oversized.name}: supera el límite de ${formatSize(maxSize)}.`;
                }
            }
            return "";
        };

        const receiveFiles = (rawFiles) => {
            const files = Array.from(rawFiles || []);
            const error = validateFiles(files);
            if (error) {
                input.value = "";
                zone.classList.remove("is-selected");
                zone.classList.add("is-invalid");
                if (title) title.textContent = "No se pudo agregar el archivo";
                showStatus(error, true);
                clear?.classList.remove("hidden");
                return;
            }

            if (!setInputFiles(input, files)) {
                zone.classList.add("is-invalid");
                showStatus("Tu navegador no pudo preparar el archivo. Haz clic para elegirlo.", true);
                return;
            }
            renderSelection(files);
        };

        zone.addEventListener("dragenter", (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            dragDepth += 1;
            zone.classList.add("is-dragging");
        });

        zone.addEventListener("dragover", (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            zone.classList.add("is-dragging");
        });

        zone.addEventListener("dragleave", (event) => {
            if (!hasFiles(event)) return;
            if (event.relatedTarget && zone.contains(event.relatedTarget)) return;
            resetDragState();
        });

        zone.addEventListener("drop", (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            resetDragState();
            receiveFiles(event.dataTransfer.files);
        });

        input.addEventListener("change", () => {
            receiveFiles(input.files);
        });

        clear?.addEventListener("click", clearSelection);

        form?.addEventListener("submit", (event) => {
            if (!input.files.length) {
                return;
            }
            if (submit) {
                submit.disabled = true;
                submit.classList.add("opacity-75", "cursor-wait");
                submit.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Preparando archivos…';
            }
            zone.classList.add("is-uploading");
            showStatus("Preparando archivos para analizar…");
        });
    });
})();
