document.addEventListener('DOMContentLoaded', () => {
    const editor = document.getElementById('lesson-editor');
    const addImageBtn = document.getElementById('add-image-btn');
    const imageUploadInput = document.getElementById('image-upload-input');
    const lessonForm = document.getElementById('lesson-form');
    const scriptInput = document.getElementById('script-input');
    
    // This will hold the files to be submitted with the final form
    const fileStore = new DataTransfer();

    // 1. Handle the "Add Image" button click
    addImageBtn.addEventListener('click', () => {
        imageUploadInput.click();
    });

    // 2. Handle the file selection
    imageUploadInput.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file) {
            // Add the file to our store for final submission
            fileStore.items.add(file);
            // Insert a simple tag and a visual preview into the editor
            insertImageTagInEditor(file);
        }
        // Clear the input so the same file can be added again if needed
        event.target.value = '';
    });

    // 3. Insert the simplified tag and a local preview into the editor
    function insertImageTagInEditor(file) {
        const altText = prompt("Please enter a short description for this image (for screen readers):", "");
        if (altText === null) {
             // If user cancels, remove the file we just added
            fileStore.items.remove(fileStore.items.length - 1);
            return;
        }

        // This is the simplified tag our parser will read
        const imageTag = `[IMAGE: alt="${altText}"]`;
        
        // Create a local URL for instant preview without uploading
        const previewUrl = URL.createObjectURL(file);
        const previewImg = document.createElement('img');
        previewImg.src = previewUrl;
        previewImg.alt = altText;
        previewImg.style.maxWidth = '200px';
        previewImg.style.display = 'block';
        previewImg.style.margin = '10px 0';
        previewImg.setAttribute('contenteditable', 'false');

        // Insert at the current cursor position
        const selection = window.getSelection();
        if (selection.getRangeAt && selection.rangeCount) {
            const range = selection.getRangeAt(0);
            range.deleteContents();
            const fragment = document.createDocumentFragment();
            fragment.appendChild(document.createTextNode(imageTag));
            fragment.appendChild(document.createElement('br'));
            fragment.appendChild(previewImg);
            fragment.appendChild(document.createElement('br'));
            range.insertNode(fragment);
        }
    }

    // 4. Before submitting, clean up the editor content for the parser
    lessonForm.addEventListener('submit', (event) => {
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = editor.innerHTML;
        tempDiv.querySelectorAll('img').forEach(img => img.remove());
        let scriptText = tempDiv.innerHTML.replace(/<br\s*[\/]?>/gi, "\n");
        scriptText = scriptText.replace(/<[^>]*>?/gm, '');
        scriptInput.value = scriptText;

        // Attach the collected files to the hidden file input for submission
        imageUploadInput.files = fileStore.files;
    });
});