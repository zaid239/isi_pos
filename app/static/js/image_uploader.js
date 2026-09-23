/**
 * Universal Image Upload & Cropping Component
 * - Client-side validation (extension, max 10MB, SVG forbidden)
 * - Offline Cropper.js modal (item: 1:1 800x800, logo: free max 400px, favicon: 1:1 64x64)
 * - Dimension badge, Rotate, Reset, Cancel, Use photo
 * - Canvas export as WebP (or JPEG) for products/favicons, PNG for logos with transparency
 * - Instant preview, dimmed uploading state with determinate progress bar (xhr.upload.onprogress)
 * - Brief checkmark badge on success, stored size display
 * - Error overlay with Retry button keeping preview intact
 * - Hover overlay with Change and Remove actions
 * - Temporary image upload & promotion support for item creation
 */
(function() {
    "use strict";

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="csrf_token"]');
        if (input && input.value) return input.value;
        return "";
    }

    let activeCropper = null;
    let pendingCropCallback = null;

    function initCropperModal() {
        const modalEl = document.getElementById("imageCropperModal");
        if (!modalEl) return null;

        const sourceImg = document.getElementById("cropperSourceImage");
        const dimBadge = document.getElementById("cropperOutputDimBadge");
        const rotateBtn = document.getElementById("cropperRotateBtn");
        const resetBtn = document.getElementById("cropperResetBtn");
        const applyBtn = document.getElementById("cropperApplyBtn");

        if (rotateBtn) {
            rotateBtn.onclick = function() {
                if (activeCropper) activeCropper.rotate(90);
            };
        }

        if (resetBtn) {
            resetBtn.onclick = function() {
                if (activeCropper) activeCropper.reset();
            };
        }

        modalEl.addEventListener("hidden.bs.modal", function() {
            if (activeCropper) {
                activeCropper.destroy();
                activeCropper = null;
            }
            if (sourceImg) sourceImg.src = "";
            pendingCropCallback = null;
        });

        return {
            modalEl,
            sourceImg,
            dimBadge,
            applyBtn,
            bsModal: (window.bootstrap && window.bootstrap.Modal) ? window.bootstrap.Modal.getOrCreateInstance(modalEl) : null
        };
    }

    function openCropper(file, imageKind, onCropped) {
        const cropperSetup = initCropperModal();
        if (!cropperSetup || !window.Cropper) {
            // Fallback if modal or Cropper is unavailable: pass raw file directly
            onCropped(file, URL.createObjectURL(file));
            return;
        }

        const { modalEl, sourceImg, dimBadge, applyBtn, bsModal } = cropperSetup;
        const objectUrl = URL.createObjectURL(file);
        sourceImg.src = objectUrl;
        pendingCropCallback = onCropped;

        function updateLogoBadge(detail) {
            if (!dimBadge) return;
            let w = detail.width;
            let h = detail.height;
            const maxDim = 400;
            if (w > maxDim || h > maxDim) {
                if (w >= h) {
                    h = Math.round((h * maxDim) / w);
                    w = maxDim;
                } else {
                    w = Math.round((w * maxDim) / h);
                    h = maxDim;
                }
            }
            dimBadge.innerHTML = `Output: ${Math.round(w)} &times; ${Math.round(h)} px (max 400px)`;
        }

        function onModalShown() {
            modalEl.removeEventListener("shown.bs.modal", onModalShown);

            let aspectRatio = 1;
            const isWordmark = (imageKind === "wordmark" || imageKind === "brand_wordmark" || imageKind === "logo");
            const isIcon = (imageKind === "icon" || imageKind === "brand_icon" || imageKind === "favicon");

            if (isWordmark) {
                aspectRatio = NaN; // Free aspect ratio for wordmark
            } else if (isIcon) {
                aspectRatio = 1; // Strict 1:1 square crop for icon
            } else {
                aspectRatio = 1; // 1:1 square for catalog items
            }

            if (dimBadge) {
                if (imageKind === "item") {
                    dimBadge.innerHTML = "Output: 800 &times; 800 px";
                } else if (imageKind === "favicon") {
                    dimBadge.innerHTML = "Output: 64 &times; 64 px";
                } else if (isIcon) {
                    dimBadge.innerHTML = "Output: 1:1 Square Crop (PNG with transparency)";
                } else if (isWordmark) {
                    dimBadge.innerHTML = "Output: Free Aspect (PNG with transparency)";
                } else {
                    dimBadge.innerHTML = "Output: max 400px (free aspect)";
                }
            }

            activeCropper = new window.Cropper(sourceImg, {
                aspectRatio: aspectRatio,
                viewMode: 1,
                dragMode: "move",
                autoCropArea: 0.95,
                restore: false,
                guides: true,
                center: true,
                highlight: false,
                cropBoxMovable: true,
                cropBoxResizable: true,
                checkOrientation: true, // EXIF auto-transposition handling
                ready: function() {
                    if (isWordmark && activeCropper) {
                        updateLogoBadge(activeCropper.getData());
                    }
                },
                crop: function(e) {
                    if (isWordmark) {
                        updateLogoBadge(e.detail);
                    }
                }
            });
        }

        modalEl.addEventListener("shown.bs.modal", onModalShown);

        applyBtn.onclick = function() {
            if (!activeCropper) return;

            let canvas;
            let exportMime = "image/webp";
            const quality = 0.85;

            const isWordmark = (imageKind === "wordmark" || imageKind === "brand_wordmark" || imageKind === "logo");
            const isIcon = (imageKind === "icon" || imageKind === "brand_icon");

            if (isWordmark) {
                // Wordmark: preserve transparency as PNG, free aspect ratio
                exportMime = "image/png";
                const cropData = activeCropper.getData();
                let w = cropData.width;
                let h = cropData.height;
                const maxDim = 1200;
                if (w > maxDim || h > maxDim) {
                    if (w >= h) {
                        h = Math.round((h * maxDim) / w);
                        w = maxDim;
                    } else {
                        w = Math.round((w * maxDim) / h);
                        h = maxDim;
                    }
                }
                canvas = activeCropper.getCroppedCanvas({
                    width: Math.round(w),
                    height: Math.round(h),
                    imageSmoothingEnabled: true,
                    imageSmoothingQuality: "high"
                });
            } else if (isIcon) {
                // Brand Icon: 1:1 square crop, preserve transparency as PNG
                exportMime = "image/png";
                const cropData = activeCropper.getData();
                let dim = Math.min(Math.round(cropData.width), 800);
                canvas = activeCropper.getCroppedCanvas({
                    width: dim,
                    height: dim,
                    imageSmoothingEnabled: true,
                    imageSmoothingQuality: "high"
                });
            } else if (imageKind === "favicon") {
                canvas = activeCropper.getCroppedCanvas({
                    width: 64,
                    height: 64,
                    imageSmoothingEnabled: true,
                    imageSmoothingQuality: "high"
                });
            } else {
                // Product item: 800x800
                canvas = activeCropper.getCroppedCanvas({
                    width: 800,
                    height: 800,
                    imageSmoothingEnabled: true,
                    imageSmoothingQuality: "high"
                });
            }

            if (!canvas) {
                if (bsModal) bsModal.hide();
                return;
            }

            // Export canvas as blob
            canvas.toBlob(function(blob) {
                if (!blob) {
                    // Fallback to JPEG if WebP blob generation is unsupported
                    exportMime = (imageKind === "logo") ? "image/png" : "image/jpeg";
                    canvas.toBlob(function(fallbackBlob) {
                        if (bsModal) bsModal.hide();
                        if (pendingCropCallback) {
                            pendingCropCallback(fallbackBlob, canvas.toDataURL(exportMime, quality));
                        }
                    }, exportMime, quality);
                    return;
                }

                if (bsModal) bsModal.hide();
                if (pendingCropCallback) {
                    pendingCropCallback(blob, canvas.toDataURL(exportMime, quality));
                }
            }, exportMime, quality);
        };

        if (bsModal) {
            bsModal.show();
        }
    }

    function initImageUploadZone(zone) {
        const dropArea = zone.querySelector(".image-drop-area");
        const fileInput = zone.querySelector(".image-upload-input");
        const previewBox = zone.querySelector(".preview-box");
        let previewImg = zone.querySelector(".preview-image");
        const placeholder = zone.querySelector(".preview-placeholder");
        const removeBtn = zone.querySelector(".btn-remove-image");
        const progressOverlay = zone.querySelector(".upload-progress-overlay");
        const progressBar = zone.querySelector(".progress-bar");
        const progressLabel = zone.querySelector(".progress-label");
        const errorBanner = zone.querySelector(".upload-error-msg");
        const successBanner = zone.querySelector(".upload-success-msg");

        // Hidden input for temporary image promotion on Create page
        const formEl = zone.closest("form");
        const tempInput = (formEl ? formEl.querySelector('input[name="temp_image_path"]') : null) || zone.querySelector('input[name="temp_image_path"]');
        const removeFlag = zone.querySelector('input[name="remove_image"]');

        const uploadUrl = zone.dataset.uploadUrl || "";
        const removeUrl = zone.dataset.removeUrl || "";
        const allowedExts = (zone.dataset.allowedExts || "png,jpg,jpeg,webp,ico").toLowerCase().split(",");
        const maxSizeMb = parseFloat(zone.dataset.maxSizeMb || "10.0");
        const maxSizeBytes = maxSizeMb * 1024 * 1024;
        const fieldName = zone.dataset.fieldName || "image";
        const imageKind = zone.dataset.imageKind || "item";

        let lastCroppedBlob = null;
        let lastCroppedDataUrl = null;

        // Ensure Retry Box element exists
        let retryBox = dropArea ? dropArea.querySelector(".upload-retry-box") : null;
        if (!retryBox && dropArea) {
            retryBox = document.createElement("div");
            retryBox.className = "upload-retry-box d-none position-absolute";
            retryBox.innerHTML = `
                <div class="retry-error-text small fw-semibold mb-2">Upload failed</div>
                <button type="button" class="btn btn-sm btn-light text-danger fw-bold btn-retry-upload">
                    <i class="bi bi-arrow-clockwise me-1"></i>Retry
                </button>
            `;
            dropArea.appendChild(retryBox);
        }
        const retryBtn = retryBox ? retryBox.querySelector(".btn-retry-upload") : null;

        // Ensure Success Checkmark Badge exists
        let successBadge = dropArea ? dropArea.querySelector(".upload-success-badge") : null;
        if (!successBadge && dropArea) {
            successBadge = document.createElement("div");
            successBadge.className = "upload-success-badge d-none position-absolute";
            successBadge.style.zIndex = "14";
            successBadge.innerHTML = `<i class="bi bi-check-lg"></i> Uploaded`;
            dropArea.appendChild(successBadge);
        }

        // Ensure Hover Action Overlay exists (Change / Remove on hover)
        let hoverOverlay = dropArea ? dropArea.querySelector(".image-hover-overlay") : null;
        if (!hoverOverlay && dropArea) {
            hoverOverlay = document.createElement("div");
            hoverOverlay.className = "image-hover-overlay";
            hoverOverlay.innerHTML = `
                <button type="button" class="btn btn-sm btn-light btn-hover-change shadow-sm">
                    <i class="bi bi-arrow-repeat me-1"></i>Change
                </button>
                <button type="button" class="btn btn-sm btn-danger btn-hover-remove shadow-sm">
                    <i class="bi bi-trash3 me-1"></i>Remove
                </button>
            `;
            dropArea.appendChild(hoverOverlay);
        }
        const hoverChangeBtn = hoverOverlay ? hoverOverlay.querySelector(".btn-hover-change") : null;
        const hoverRemoveBtn = hoverOverlay ? hoverOverlay.querySelector(".btn-hover-remove") : null;

        // Ensure Stored Size Badge element exists
        let storedSizeBadge = zone.querySelector(".stored-size-badge");
        if (!storedSizeBadge) {
            storedSizeBadge = document.createElement("div");
            storedSizeBadge.className = "stored-size-badge text-muted small mt-1";
            const feedback = zone.querySelector(".upload-feedback");
            if (feedback) feedback.appendChild(storedSizeBadge);
        }

        // Check if an initial image is already loaded
        if (previewImg && previewImg.src && !previewImg.src.endsWith("/") && !previewImg.classList.contains("d-none") && previewImg.getAttribute("src") !== "") {
            dropArea.classList.add("has-image");
            if (removeBtn) removeBtn.classList.remove("d-none");
        }

        function showMessage(msg, isError = true) {
            if (isError) {
                if (errorBanner) {
                    errorBanner.textContent = msg;
                    errorBanner.classList.remove("d-none");
                }
                if (successBanner) successBanner.classList.add("d-none");
            } else {
                if (successBanner) {
                    successBanner.textContent = msg;
                    successBanner.classList.remove("d-none");
                    setTimeout(() => {
                        if (successBanner) successBanner.classList.add("d-none");
                    }, 4000);
                }
                if (errorBanner) errorBanner.classList.add("d-none");
            }
        }

        function clearMessages() {
            if (errorBanner) {
                errorBanner.textContent = "";
                errorBanner.classList.add("d-none");
            }
            if (successBanner) {
                successBanner.textContent = "";
                successBanner.classList.add("d-none");
            }
            if (retryBox) retryBox.classList.add("d-none");
        }

        // Clicking on drop area triggers hidden file input (unless clicking a button or when image exists)
        if (dropArea && fileInput) {
            dropArea.addEventListener("click", function(e) {
                if (e.target.closest("button") || e.target.closest("a") || e.target.closest(".upload-retry-box")) return;
                if (dropArea.classList.contains("has-image")) {
                    // When image already exists, hover overlay buttons are used
                    return;
                }
                fileInput.click();
            });
        }

        if (hoverChangeBtn && fileInput) {
            hoverChangeBtn.addEventListener("click", function(e) {
                e.preventDefault();
                e.stopPropagation();
                fileInput.click();
            });
        }

        if (hoverRemoveBtn) {
            hoverRemoveBtn.addEventListener("click", function(e) {
                e.preventDefault();
                e.stopPropagation();
                triggerRemove();
            });
        }

        if (retryBtn) {
            retryBtn.addEventListener("click", function(e) {
                e.preventDefault();
                e.stopPropagation();
                if (lastCroppedBlob) {
                    uploadBlobViaAjax(lastCroppedBlob);
                }
            });
        }

        // Drag & Drop event handlers
        if (dropArea) {
            ["dragenter", "dragover"].forEach(eventName => {
                dropArea.addEventListener(eventName, function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    dropArea.classList.add("dragover");
                }, false);
            });

            ["dragleave", "drop"].forEach(eventName => {
                dropArea.addEventListener(eventName, function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    dropArea.classList.remove("dragover");
                }, false);
            });

            dropArea.addEventListener("drop", function(e) {
                const dt = e.dataTransfer;
                if (dt && dt.files && dt.files.length > 0) {
                    handleRawFileSelection(dt.files[0]);
                }
            });
        }

        if (fileInput) {
            fileInput.addEventListener("change", function() {
                if (fileInput.files && fileInput.files.length > 0) {
                    handleRawFileSelection(fileInput.files[0]);
                }
            });
        }

        function handleRawFileSelection(file) {
            clearMessages();
            if (!file) return;

            const name = file.name.toLowerCase();

            // Client-side validation: Strictly reject SVG
            if (name.endsWith(".svg") || file.type === "image/svg+xml") {
                showMessage("SVG files are strictly forbidden due to security policies.", true);
                fileInput.value = "";
                return;
            }

            // Client-side validation: Extension whitelist
            const ext = name.includes(".") ? name.split(".").pop() : "";
            if (!allowedExts.includes(ext)) {
                showMessage(`File extension '.${ext}' is not permitted. Allowed: ${allowedExts.join(", ")}.`, true);
                fileInput.value = "";
                return;
            }

            // Client-side validation: Max size limit (10MB)
            if (file.size > maxSizeBytes) {
                const actualMb = (file.size / (1024 * 1024)).toFixed(2);
                showMessage(`File size (${actualMb} MB) exceeds maximum allowed limit of ${maxSizeMb} MB.`, true);
                fileInput.value = "";
                return;
            }

            if (file.size === 0) {
                showMessage("Selected file is empty.", true);
                fileInput.value = "";
                return;
            }

            // Launch Cropper.js modal
            openCropper(file, imageKind, function(croppedBlob, dataUrl) {
                lastCroppedBlob = croppedBlob;
                lastCroppedDataUrl = dataUrl;

                // 1. Instant Facebook-style preview
                applyPreview(dataUrl);

                // 2. Determinate AJAX upload with progress bar
                if (uploadUrl) {
                    uploadBlobViaAjax(croppedBlob);
                }
            });
        }

        function applyPreview(url) {
            if (previewImg) {
                const picParent = previewImg.closest("picture");
                if (picParent) {
                    picParent.querySelectorAll("source").forEach(s => s.srcset = url);
                }
                previewImg.src = url;
                previewImg.classList.remove("d-none");
            }
            if (placeholder) placeholder.classList.add("d-none");
            if (dropArea) dropArea.classList.add("has-image");
            if (removeBtn) removeBtn.classList.remove("d-none");
            if (retryBox) retryBox.classList.add("d-none");
            if (removeFlag) removeFlag.value = "0";
        }

        function uploadBlobViaAjax(blob) {
            clearMessages();
            const formData = new FormData();
            const isPng = (imageKind === "logo" || imageKind === "brand_wordmark" || imageKind === "wordmark" || imageKind === "brand_icon" || imageKind === "icon");
            const ext = isPng ? "png" : "webp";
            const filename = `upload.${ext}`;
            formData.append(fieldName, blob, filename);

            const xhr = new XMLHttpRequest();
            xhr.open("POST", uploadUrl, true);
            xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
            const token = getCsrfToken();
            if (token) xhr.setRequestHeader("X-CSRFToken", token);

            // Set dimmed preview & determinate progress
            if (dropArea) dropArea.classList.add("uploading");
            if (progressOverlay) progressOverlay.classList.remove("d-none");
            if (progressBar) progressBar.style.width = "0%";
            if (progressLabel) progressLabel.textContent = "Uploading... 0%";

            xhr.upload.onprogress = function(e) {
                if (e.lengthComputable) {
                    const percent = Math.round((e.loaded / e.total) * 100);
                    if (progressBar) progressBar.style.width = percent + "%";
                    if (progressLabel) progressLabel.textContent = `Uploading... ${percent}%`;
                }
            };

            xhr.onload = function() {
                if (dropArea) dropArea.classList.remove("uploading");
                if (progressOverlay) progressOverlay.classList.add("d-none");

                if (xhr.status >= 200 && xhr.status < 300) {
                    try {
                        const res = JSON.parse(xhr.responseText);
                        if (res.success) {
                            // Instant brightness restored, show brief green checkmark
                            if (successBadge) {
                                successBadge.classList.remove("d-none");
                                setTimeout(() => {
                                    if (successBadge) successBadge.classList.add("d-none");
                                }, 2500);
                            }

                            // Show stored size
                            if (storedSizeBadge && res.file_size_formatted) {
                                const fmt = isPng ? "PNG" : "WebP";
                                storedSizeBadge.textContent = `Stored: ${res.file_size_formatted} (${fmt})`;
                            }

                            // If this was temp upload for item creation, save temp path in hidden input
                            if (res.temp_path && tempInput) {
                                tempInput.value = res.temp_path;
                            }

                            if (res.url && previewImg) {
                                const picParent = previewImg.closest("picture");
                                if (picParent) {
                                    picParent.querySelectorAll("source").forEach(s => s.srcset = res.url);
                                }
                                previewImg.src = res.fallback_url || res.url;
                            }

                            // Update companion previews (e.g. white preview or favicon thumbnail)
                            const whitePreview = zone.querySelector(".preview-image-white");
                            if (whitePreview && res.white_url) {
                                whitePreview.src = res.white_url;
                                whitePreview.classList.remove("d-none");
                            }
                            const favPreview = zone.querySelector(".preview-image-favicon");
                            if (favPreview && res.favicon_url) {
                                favPreview.src = res.favicon_url;
                                favPreview.classList.remove("d-none");
                            }

                            showMessage(res.message || "Photo saved.", false);
                        } else {
                            handleUploadError(res.error || "Upload failed.");
                        }
                    } catch (_) {
                        showMessage("Photo uploaded successfully.", false);
                    }
                } else {
                    let errMsg = "Upload failed.";
                    try {
                        const res = JSON.parse(xhr.responseText);
                        if (res.error) errMsg = res.error;
                    } catch (_) {}
                    handleUploadError(errMsg);
                }
            };

            xhr.onerror = function() {
                if (dropArea) dropArea.classList.remove("uploading");
                if (progressOverlay) progressOverlay.classList.add("d-none");
                handleUploadError("Network connection error. Upload could not complete.");
            };

            xhr.send(formData);
        }

        function handleUploadError(errMsg) {
            // Keep preview visible, display error with Retry button
            if (retryBox) {
                const textEl = retryBox.querySelector(".retry-error-text");
                if (textEl) textEl.textContent = errMsg;
                retryBox.classList.remove("d-none");
            }
            showMessage(errMsg, true);
        }

        function triggerRemove() {
            const confirmMsg = zone.dataset.confirmRemove || "Are you sure you want to remove this photo? The file will be permanently deleted.";
            if (!window.confirm(confirmMsg)) return;

            clearMessages();

            // If a temp file was uploaded on create page, call remove-temp-image
            if (tempInput && tempInput.value) {
                const curTempPath = tempInput.value;
                const tempRemoveUrl = zone.dataset.tempRemoveUrl || "/items/remove-temp-image";
                const xhr = new XMLHttpRequest();
                xhr.open("POST", tempRemoveUrl, true);
                xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
                const token = getCsrfToken();
                if (token) xhr.setRequestHeader("X-CSRFToken", token);
                const fd = new FormData();
                fd.append("temp_path", curTempPath);
                xhr.send(fd);
                tempInput.value = "";
                revertToPlaceholder();
                showMessage("Photo removed.", false);
                return;
            }

            // If an AJAX remove URL is configured (edit page or branding settings)
            if (removeUrl) {
                const xhr = new XMLHttpRequest();
                xhr.open("POST", removeUrl, true);
                xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
                const token = getCsrfToken();
                if (token) xhr.setRequestHeader("X-CSRFToken", token);

                xhr.onload = function() {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        revertToPlaceholder();
                        showMessage("Photo removed successfully.", false);
                    } else {
                        let errMsg = "Failed to remove photo.";
                        try {
                            const res = JSON.parse(xhr.responseText);
                            if (res.error) errMsg = res.error;
                        } catch (_) {}
                        showMessage(errMsg, true);
                    }
                };

                xhr.onerror = function() {
                    showMessage("Network error while removing photo.", true);
                };

                xhr.send();
            } else {
                // Client-only remove
                revertToPlaceholder();
                if (removeFlag) removeFlag.value = "1";
                showMessage("Photo cleared.", false);
            }
        }

        if (removeBtn) {
            removeBtn.addEventListener("click", function(e) {
                e.preventDefault();
                e.stopPropagation();
                triggerRemove();
            });
        }

        function revertToPlaceholder() {
            if (fileInput) fileInput.value = "";
            lastCroppedBlob = null;
            lastCroppedDataUrl = null;

            if (previewImg) {
                const picParent = previewImg.closest("picture");
                if (picParent) {
                    picParent.querySelectorAll("source").forEach(s => s.srcset = "");
                }
                previewImg.src = "";
                previewImg.classList.add("d-none");
            }
            if (placeholder) placeholder.classList.remove("d-none");
            if (dropArea) {
                dropArea.classList.remove("has-image");
                dropArea.classList.remove("uploading");
            }
            if (removeBtn) removeBtn.classList.add("d-none");
            if (retryBox) retryBox.classList.add("d-none");
            if (storedSizeBadge) storedSizeBadge.textContent = "";
            if (tempInput) tempInput.value = "";
        }
    }

    document.addEventListener("DOMContentLoaded", function() {
        document.querySelectorAll(".image-upload-zone").forEach(initImageUploadZone);
    });
})();
