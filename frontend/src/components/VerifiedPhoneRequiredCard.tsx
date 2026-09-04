import { Link } from "react-router-dom";

type VerifiedPhoneRequiredCardProps = {
  eyebrow?: string;
  title?: string;
  description?: string;
  buttonText?: string;
};

function VerifiedPhoneRequiredCard({
  eyebrow = "პროფილი",
  title = "ტელეფონის დადასტურება საჭიროა",
  description = "ამ გვერდის სანახავად ჯერ უნდა დაადასტუროთ ტელეფონის ნომერი SMS კოდით.",
  buttonText = "ტელეფონის დადასტურება",
}: VerifiedPhoneRequiredCardProps) {
  return (
    <section className="card">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="muted">{description}</p>

      <Link className="button-link" to="/profile">
        {buttonText}
      </Link>
    </section>
  );
}

export default VerifiedPhoneRequiredCard;
