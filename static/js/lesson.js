document.addEventListener('DOMContentLoaded', () => {
    const chatBox = document.getElementById('chat-box');
    const inputArea = document.getElementById('input-area');
    const systemMessage = document.getElementById('system-message');
    const qnaButton = document.getElementById('ask-qna-btn');

    let currentStep = 0;
    let isWaitingForResponse = false;
    let chatHistory = [];

    qnaButton.addEventListener('click', () => {
        if (isWaitingForResponse) return;
        const question = prompt("What is your question about the lesson so far?");
        if (question && question.trim() !== "") {
            addMessage(question, 'student');
            updateHistory('user', question);
            postToChat(question, 'QNA');
        }
    });

    function addMessage(text, sender) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-message`;
    
    // ADD THIS IF-STATEMENT
    // If the message is from the tutor and contains HTML, treat it as the main content
    if (sender === 'tutor' && /<[a-z][\s\S]*>/i.test(text)) {
        messageDiv.classList.add('main-content-block');
        // Use the reader theme for consistent styling
        const styleLink = document.createElement('link');
        styleLink.rel = 'stylesheet';
        styleLink.href = '/static/css/reader_theme.css';
        messageDiv.appendChild(styleLink);
    }
    
    messageDiv.innerHTML += text; // Use += to not overwrite the style link
    chatBox.appendChild(messageDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}

    function updateHistory(role, text) {
        chatHistory.push({ role: role, parts: [{ text: text }] });
    }

    function addMediaMessage(url, alt) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message tutor-message media-message';
        const img = document.createElement('img');
        img.src = url;
        img.alt = alt;
        messageDiv.appendChild(img);
        chatBox.appendChild(messageDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function showMCQOptions(questionData) {
        inputArea.innerHTML = '';
        const questionText = document.createElement('p');
        questionText.innerText = questionData.question;
        inputArea.appendChild(questionText);
        const optionsContainer = document.createElement('div');
        optionsContainer.className = 'options-container';
        for (const key in questionData.options) {
            const button = document.createElement('button');
            button.innerText = `${key}: ${questionData.options[key]}`;
            button.dataset.answer = key;
            button.addEventListener('click', () => {
                if (isWaitingForResponse) return;
                const answerText = `${key}: ${questionData.options[key]}`;
                addMessage(answerText, 'student');
                updateHistory('user', `Selected answer: ${answerText}`);
                postToChat(key, 'LESSON_FLOW');
            });
            optionsContainer.appendChild(button);
        }
        inputArea.appendChild(optionsContainer);
    }

    function showShortAnswerInput(questionData) {
        inputArea.innerHTML = '';
        const questionText = document.createElement('p');
        questionText.innerText = questionData.question;
        inputArea.appendChild(questionText);
        const answerTextarea = document.createElement('textarea');
        answerTextarea.rows = 3;
        answerTextarea.placeholder = "Type your answer here...";
        inputArea.appendChild(answerTextarea);
        const submitButton = document.createElement('button');
        submitButton.innerText = 'Submit Answer';
        submitButton.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            const answer = answerTextarea.value.trim();
            if (answer === "") { alert("Please type an answer."); return; }
            addMessage(answer, 'student');
            updateHistory('user', answer);
            postToChat(answer, 'LESSON_FLOW');
        });
        inputArea.appendChild(submitButton);
    }

    async function postToChat(userInput = null, requestType = 'LESSON_FLOW') {
        isWaitingForResponse = true;
        systemMessage.innerText = 'Guidee is thinking...';
        systemMessage.style.display = 'block';
        if (requestType === 'LESSON_FLOW') {
            inputArea.innerHTML = '';
        }

        const requestBody = {
            lesson_id: LESSON_ID,
            step_index: currentStep,
            user_input: userInput,
            chat_history: chatHistory,
            request_type: requestType
        };
        console.log("--> Sending request to /chat:", requestBody);

        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();
        console.log("<-- Received response from /chat:", data);

        isWaitingForResponse = false;
        systemMessage.style.display = 'none';

        if (data.feedback) { addMessage(data.feedback, 'tutor'); updateHistory('model', data.feedback); }
        if (data.media_url) { addMediaMessage(data.media_url, "Lesson media"); }
        if (data.tutor_text) { addMessage(data.tutor_text, 'tutor'); updateHistory('model', data.tutor_text); }

        if (data.is_qna_response) {
            postToChat(null, 'LESSON_FLOW');
            return;
        }

        let nextInteractionScheduled = false;
        if (data.is_lesson_end) {
            // Handle course completion and certificate link
            if (data.certificate_url) {
                const certButton = document.createElement('a');
                certButton.href = data.certificate_url;
                certButton.innerText = 'View Your Certificate!';
                certButton.className = 'btn btn-primary';
                certButton.style.marginTop = '10px';
                inputArea.appendChild(certButton);

            } else if (data.next_chapter_url) {
                const nextChapterButton = document.createElement('button');
                nextChapterButton.innerText = 'Go to Next Chapter';
                nextChapterButton.className = 'btn btn-primary';
                nextChapterButton.addEventListener('click', () => {
                    window.location.href = data.next_chapter_url;
                });
                inputArea.appendChild(nextChapterButton);
            }
            nextInteractionScheduled = true;
        } else if (data.question) {
            if (data.question.type === 'QUESTION_MCQ') {
                showMCQOptions(data.question);
            } else if (data.question.type === 'QUESTION_SA') {
                showShortAnswerInput(data.question);
            }
            nextInteractionScheduled = true;
        }

        if (!nextInteractionScheduled) {
            const continueButton = document.createElement('button');
            continueButton.innerText = 'Continue';
            continueButton.addEventListener('click', () => {
                if (isWaitingForResponse) return;
                    const continueText = 'Continue';
                    addMessage(continueText, 'student'); // Show "Continue" in the chat
                    updateHistory('user', continueText);
                    postToChat(continueText, 'LESSON_FLOW'); // Send it as input
            });
            inputArea.appendChild(continueButton);
        }
        
        currentStep = data.next_step;
    
    }

    postToChat(null, 'LESSON_FLOW');
});